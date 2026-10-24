"""Заявка глазами клиента: черновик → согласия → отправка → уточнения → предложение → подпись."""

import hmac
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.config import get_settings
from app.db import get_db
from app.domain.enums import (
    CONSENT_TEXTS,
    DOC_TYPES,
    POSITION_LABELS,
    PURPOSES,
    TERMINAL_STATUSES,
    ActorType,
    AppStatus,
    ConsentType,
    InfoRequestStatus,
    JobStage,
    OfferStatus,
    Role,
    SignPurpose,
)
from app.domain.products import pledged, required_documents
from app.domain.statuses import CLIENT_CANCELLABLE, CLIENT_STATUS
from app.domain.text import fmt_amount, fmt_money, fmt_rate, months_range
from app.models import (
    Application,
    ApplicationDocument,
    Company,
    CompanyMember,
    Consent,
    Decision,
    InfoRequest,
    Loan,
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
    ReapplyIn,
    SignatureRequestIn,
    SignChallengeIn,
    SignChallengeOut,
    SignConfirmIn,
    SimulationIn,
    SimulationOut,
    SubmitIn,
)
from app.security import get_client_application, hash_code, require_company_member, require_roles
from app.services import assessment, storage
from app.services.simulation import SimulationError, Simulator, describe
from app.services.views import (
    application_out,
    can_cancel,
    document_out,
    message_out,
    offer_out,
    proposed_conditions,
    revenue_estimate,
    schedule_for,
    summary_out,
)
from app.services.workflow import (
    add_event,
    enqueue,
    lock_key,
    next_application_number,
    notify_company,
    transition,
    utcnow,
)

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
SIGN_CODES_PER_10_MIN = 10
# Открытые заявки, которые можно заменить новой одним действием (повторная подача с replace_current)
REPLACEABLE = (AppStatus.OFFER_READY, AppStatus.MANUAL_REVIEW)
MAX_DOCUMENTS_PER_APPLICATION = 20
INFO_EDITABLE_FIELDS = {
    "declared_revenue",
    "collateral_description",
    "contact_name",
    "contact_phone",
    "purpose_details",
}


def _commit(db: Session) -> None:
    try:
        db.commit()
    except (StaleDataError, IntegrityError) as e:
        # параллельный запрос успел изменить заявку (или уже выполнил то же действие)
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Заявка изменилась, обновите страницу") from e


def _out(db: Session, app: Application, member: CompanyMember) -> dict:
    db.refresh(app)
    return application_out(db, app, member)


def _require_status(app: Application, *allowed: AppStatus) -> None:
    if app.status not in allowed:
        title = CLIENT_STATUS[AppStatus(app.status)][0]
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Действие недоступно: заявка в статусе «{title}». Обновите страницу"
        )


def _require_signer(member: CompanyMember, action: str = "Подписывать", hint: bool = True) -> None:
    """Юридически значимые действия от имени компании — только у лиц с правом подписи."""
    if not member.can_sign:
        text = f"{action} может руководитель или представитель по доверенности."
        if hint:
            text += " Нажмите «Передать на подпись», и мы уведомим руководителя."
        raise HTTPException(status.HTTP_403_FORBIDDEN, text)


# --------------------------------------------------------------------------- черновик


@router.get("", response_model=list[ApplicationSummaryOut], summary="Заявки компании")
def list_applications(company_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    require_company_member(db, user, company_id)
    apps = db.scalars(
        select(Application).where(Application.company_id == company_id).order_by(Application.created_at.desc())
    )
    return [summary_out(a) for a in apps]


@router.post(
    "",
    response_model=ApplicationOut,
    status_code=201,
    summary="Создать черновик заявки (данные компании подставляются автоматически)",
)
def create_application(body: ApplicationCreate, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = _new_application(db, user, body)
    _commit(db)
    return _out(db, app, member)


def _new_application(
    db: Session, user: User, body: ApplicationCreate, parent: Application | None = None
) -> tuple[Application, CompanyMember]:
    """Новый черновик. parent — закрытая заявка, на основе которой подают повторно (только из reapply)."""
    member = require_company_member(db, user, body.company_id)
    product = db.get(Product, body.product_code)
    if product is None or not product.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Продукт не найден")
    if body.purpose not in PURPOSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неизвестная цель кредита")
    company = db.get(Company, body.company_id)
    stats = assessment.company_turnover(db, company.id)
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
        has_collateral=pledged(product, bool(parent and parent.has_collateral)),
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
    return app, member


@router.get("/{app_id}", response_model=ApplicationOut, summary="Заявка: статус, что дальше, предложения, история")
def get_application(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    return application_out(db, app, member)


@router.patch("/{app_id}", response_model=ApplicationOut, summary="Изменить черновик (автосохранение)")
def update_application(
    app_id: uuid.UUID, body: ApplicationUpdate, db: Session = Depends(get_db), user: User = Depends(client_only)
):
    app, member = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT)
    data = body.model_dump(exclude_unset=True)
    if "product_code" in data:
        product = db.get(Product, data["product_code"])
        if product is None or not product.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Продукт не найден")
    if "purpose" in data and data["purpose"] not in PURPOSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неизвестная цель кредита")
    for key, value in data.items():
        if key in EDITABLE_FIELDS:
            setattr(app, key, value)
    # залог зависит от продукта: обязателен — всегда есть, у беззалогового — флаг от прежнего продукта сбрасывается
    app.has_collateral = pledged(db.get(Product, app.product_code), app.has_collateral)
    db.flush()
    db.refresh(app)
    _commit(db)
    return _out(db, app, member)


# --------------------------------------------------------------------------- документы


def _save_document(
    db: Session, app: Application, user: User, doc_type: str, file_name: str, content: bytes, content_type: str
):
    if doc_type not in DOC_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неизвестный тип документа")
    lock_key(db, f"documents:{app.id}")  # лимит документов не обходится параллельными загрузками
    count = db.scalar(
        select(func.count()).select_from(ApplicationDocument).where(ApplicationDocument.application_id == app.id)
    )
    if count >= MAX_DOCUMENTS_PER_APPLICATION:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"К заявке можно приложить не больше {MAX_DOCUMENTS_PER_APPLICATION} файлов. Удалите лишние",
        )
    try:
        key, sha = storage.save(app.id, content, content_type)
    except storage.StorageError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    doc = ApplicationDocument(
        application_id=app.id,
        doc_type=doc_type,
        file_name=storage.safe_file_name(file_name, content_type),
        content_type=content_type,
        size_bytes=len(content),
        sha256=sha,
        storage_key=key,
        uploaded_by_id=user.id,
    )
    db.add(doc)
    add_event(
        db,
        app,
        "DOCUMENT_UPLOADED",
        f"Загружен документ: {DOC_TYPES[doc_type]}",
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
    )
    _commit(db)
    return doc


@router.post("/{app_id}/documents", response_model=DocumentOut, status_code=201, summary="Загрузить документ")
def upload_document(
    app_id: uuid.UUID,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(client_only),
):
    # обычная (не async) функция, как и остальные маршруты: запись файла и запросы к БД идут в пуле потоков
    # и не останавливают цикл событий сервера на время загрузки
    app, _ = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT, AppStatus.NEEDS_INFO)
    limit = settings.max_upload_mb * 1024 * 1024
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"Файл больше {settings.max_upload_mb} МБ")
    file_name = file.filename or "document"
    content_type = storage.resolve_type(file_name, file.content_type)
    doc = _save_document(db, app, user, doc_type, file_name, content, content_type)
    return document_out(doc)


@router.post(
    "/{app_id}/documents/demo",
    response_model=DocumentOut,
    status_code=201,
    summary="Приложить пример документа (только на демо-стенде)",
)
def demo_document(app_id: uuid.UUID, doc_type: str, db: Session = Depends(get_db), user: User = Depends(client_only)):
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Недоступно")
    app, _ = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT, AppStatus.NEEDS_INFO)
    label = DOC_TYPES.get(doc_type)
    if label is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неизвестный тип документа")
    content = storage.demo_document(label, app.company.short_name, app.company.inn)
    doc = _save_document(db, app, user, doc_type, f"{label} (пример).txt", content, "text/plain")
    return document_out(doc)


@router.get("/{app_id}/documents/{doc_id}", summary="Скачать документ")
def download_document(
    app_id: uuid.UUID, doc_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)
):
    app, _ = get_client_application(db, user, app_id)
    doc = db.get(ApplicationDocument, doc_id)
    if doc is None or doc.application_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    return storage.file_response(doc)


@router.delete("/{app_id}/documents/{doc_id}", status_code=204, summary="Удалить документ")
def delete_document(
    app_id: uuid.UUID, doc_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)
):
    app, _ = get_client_application(db, user, app_id)
    _require_status(app, AppStatus.DRAFT, AppStatus.NEEDS_INFO)
    doc = db.get(ApplicationDocument, doc_id)
    if doc is None or doc.application_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    key = doc.storage_key
    add_event(
        db,
        app,
        "DOCUMENT_DELETED",
        f"Удалён документ: {DOC_TYPES.get(doc.doc_type, doc.doc_type)}",
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
        payload={"file_name": doc.file_name, "sha256": doc.sha256},
    )
    db.delete(doc)
    _commit(db)
    # файл удаляется после фиксации в БД и только если на него не ссылается другая заявка (перенос при повторной подаче)
    still_used = db.scalar(select(ApplicationDocument.id).where(ApplicationDocument.storage_key == key).limit(1))
    if still_used is None:
        storage.delete(key)


# --------------------------------------------------------------------------- подпись (простая ЭП кодом внутри продукта)


def _verify_challenge(
    db: Session, app: Application, user: User, purpose: str, challenge_id: uuid.UUID, code: str
) -> SignChallenge:
    ch = db.get(SignChallenge, challenge_id, with_for_update=True)
    if ch is None or ch.application_id != app.id or ch.user_id != user.id or ch.purpose != purpose:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Код подтверждения не найден, запросите новый")
    if ch.used_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот код уже использован")
    if ch.expires_at < utcnow():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Срок действия кода истёк, запросите новый")
    if ch.attempts >= 5:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток, запросите новый код")
    ch.attempts += 1
    if not hmac.compare_digest(hash_code(code.strip(), str(ch.id)), ch.code_hash):
        db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неверный код подтверждения")
    ch.used_at = utcnow()
    return ch


@router.post("/{app_id}/sign-challenges", response_model=SignChallengeOut, summary="Получить код подтверждения подписи")
def create_sign_challenge(
    app_id: uuid.UUID, body: SignChallengeIn, db: Session = Depends(get_db), user: User = Depends(client_only)
):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member)
    lock_key(db, f"sign:{app.id}")  # лимит кодов и попыток не обходится параллельными запросами
    recent = db.scalar(
        select(func.count())
        .select_from(SignChallenge)
        .where(
            SignChallenge.application_id == app.id,
            SignChallenge.user_id == user.id,
            SignChallenge.created_at > utcnow() - timedelta(minutes=10),
        )
    )
    if recent >= SIGN_CODES_PER_10_MIN:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов кода — попробуйте через 10 минут"
        )
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


@router.post(
    "/{app_id}/consents", response_model=ApplicationOut, summary="Подписать согласия на запрос в БКИ и обработку данных"
)
def sign_consents(
    app_id: uuid.UUID, body: SignConfirmIn, db: Session = Depends(get_db), user: User = Depends(client_only)
):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member)
    lock_key(db, f"sign:{app.id}")
    db.refresh(app)
    _require_status(app, AppStatus.DRAFT)
    existing = {c.consent_type for c in db.scalars(select(Consent).where(Consent.application_id == app.id))}
    if {ConsentType.BKI_REQUEST, ConsentType.PERSONAL_DATA} <= existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Согласия уже подписаны")
    ch = _verify_challenge(db, app, user, SignPurpose.CONSENTS, body.challenge_id, body.code)
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
    add_event(
        db,
        app,
        "CONSENTS_SIGNED",
        "Подписаны согласия на запрос в БКИ и обработку данных",
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
    )
    _commit(db)
    return _out(db, app, member)


@router.post(
    "/{app_id}/request-signature", response_model=ApplicationOut, summary="Передать заявку руководителю на подпись"
)
def request_signature(
    app_id: uuid.UUID,
    body: SignatureRequestIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(client_only),
):
    """Сотрудник без права подписи передаёт заявку руководителю. С условиями из конструктора — предлагает
    подать новую заявку на них: руководитель откроет заявку, и конструктор покажет эти условия."""
    app, member = get_client_application(db, user, app_id)
    if member.can_sign:
        raise HTTPException(status.HTTP_409_CONFLICT, "У вас есть право подписи — подпишите сами")
    position = POSITION_LABELS.get(member.position, member.position).lower()
    if body is not None and body.conditions is not None:
        _require_status(app, AppStatus.OFFER_READY, AppStatus.MANUAL_REVIEW)
        try:
            sim = Simulator(db, app)
            params = sim.params(**body.conditions.model_dump())
            product = sim.validate(params)
            params.has_collateral = pledged(product, params.has_collateral)
        except SimulationError as e:
            code = status.HTTP_409_CONFLICT if e.unavailable else status.HTTP_422_UNPROCESSABLE_CONTENT
            raise HTTPException(code, str(e)) from e
        if params == sim.base_params():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Условия совпадают с текущей заявкой — измените их"
            )
        if sim.matching_offer(params):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Этот вариант есть в предложении банка — передайте заявку руководителю, чтобы он его выбрал",
            )
        last = proposed_conditions(db, app)
        if last and last["conditions"] == params.to_dict():
            raise HTTPException(status.HTTP_409_CONFLICT, "Эти условия уже переданы руководителю")
        text = describe(product, params)
        notify_company(
            db,
            app,
            f"Новые условия по заявке № {app.number}",
            f"{user.full_name} ({position}) предлагает подать новую заявку: {text.rstrip('.')}. "
            "Откройте заявку — условия уже в конструкторе.",
            signers_only=True,
        )
        add_event(
            db,
            app,
            "CONDITIONS_PROPOSED",
            f"Руководителю переданы условия для новой заявки: {text}",
            actor_type=ActorType.CLIENT,
            actor_id=user.id,
            payload={"conditions": params.to_dict(), "text": text},
        )
        _commit(db)
        return _out(db, app, member)
    _require_status(app, AppStatus.DRAFT, AppStatus.OFFER_READY, AppStatus.SIGNING)
    what = {
        AppStatus.DRAFT: "подписать согласия и отправить заявку",
        AppStatus.OFFER_READY: "выбрать вариант предложения и подписать договор",
        AppStatus.SIGNING: "подписать кредитный договор",
    }[AppStatus(app.status)]
    notify_company(
        db,
        app,
        f"Нужна ваша подпись: заявка № {app.number}",
        f"{user.full_name} ({position}) просит {what}.",
        signers_only=True,
    )
    add_event(
        db,
        app,
        "SIGNATURE_REQUESTED",
        f"Заявка передана руководителю: {what}",
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
    )
    _commit(db)
    return _out(db, app, member)


# --------------------------------------------------------------------------- отправка


def terms_problems(app: Application) -> list[str]:
    """Сумма, срок и цель — в пределах условий продукта."""
    p = app.product
    problems = []
    if not float(p.min_amount) <= float(app.amount) <= float(p.max_amount):
        problems.append(f"Сумма должна быть от {fmt_amount(p.min_amount)} до {fmt_money(p.max_amount)}")
    if not p.min_term <= app.term_months <= p.max_term:
        problems.append(f"Срок по продукту — {months_range(p.min_term, p.max_term)}")
    if app.purpose not in p.purposes:
        problems.append("Цель не подходит для выбранного продукта")
    return problems


def submit_problems(db: Session, app: Application) -> list[str]:
    p = app.product
    amount = float(app.amount)
    problems = terms_problems(app)
    if not app.declared_revenue:
        problems.append("Укажите выручку за последние 12 месяцев")
    if not (app.contact_name and app.contact_phone):
        problems.append("Укажите контактное лицо и телефон")
    if pledged(p, app.has_collateral) and not (app.collateral_description or "").strip():
        problems.append("Опишите имущество, которое будет в залоге")
    have = {d.doc_type for d in app.documents}
    missing = [DOC_TYPES[t] for t in required_documents(p, amount, app.purpose, app.company.kind) if t not in have]
    if missing:
        problems.append("Приложите документы: " + ", ".join(missing))
    signed = {c.consent_type for c in db.scalars(select(Consent).where(Consent.application_id == app.id))}
    if not {ConsentType.BKI_REQUEST, ConsentType.PERSONAL_DATA} <= signed:
        problems.append("Подпишите согласия на запрос в БКИ и обработку данных")
    return problems


def _require_same_terms(db: Session, app: Application, body: SubmitIn | None) -> None:
    """Отправляется ровно то, что клиент видит в форме: если правка не сохранилась, условия разойдутся."""
    if body is None:
        return
    diffs = []
    if body.product_code is not None and body.product_code != app.product_code:
        in_form = db.get(Product, body.product_code)
        diffs.append(f"продукт «{app.product.name}», а в форме — «{in_form.name if in_form else body.product_code}»")
    if body.purpose is not None and body.purpose != app.purpose:
        diffs.append("другая цель кредита")
    if body.amount is not None and abs(body.amount - float(app.amount)) >= 0.5:
        diffs.append(f"сумма {fmt_money(app.amount)}, а в форме — {fmt_money(body.amount)}")
    if body.term_months is not None and body.term_months != app.term_months:
        diffs.append(f"срок {app.term_months} мес., а в форме — {body.term_months} мес.")
    sent = body.model_dump(exclude_unset=True)
    product = app.product
    if "has_collateral" in sent and pledged(product, bool(body.has_collateral)) != pledged(product, app.has_collateral):
        diffs.append(
            "в заявке есть залог, а в форме его нет"
            if pledged(product, app.has_collateral)
            else "в заявке нет залога, а в форме он есть"
        )
    if "owner_guarantee" in sent and bool(body.owner_guarantee) != bool(app.owner_guarantee):
        diffs.append(
            "в заявке есть поручительство собственника, а в форме его нет"
            if app.owner_guarantee
            else "в заявке нет поручительства собственника, а в форме оно есть"
        )
    if "declared_revenue" in sent:
        stored = float(app.declared_revenue) if app.declared_revenue else 0.0
        if abs((body.declared_revenue or 0.0) - stored) >= 0.5:
            diffs.append(f"выручка {fmt_money(stored)}, а в форме — {fmt_money(body.declared_revenue or 0)}")
    if diffs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Заявка не отправлена: в банке сохранены другие условия — "
            + "; ".join(diffs)
            + ". Обновите страницу, проверьте данные и отправьте ещё раз",
        )


@router.post("/{app_id}/submit", response_model=ApplicationOut, summary="Отправить заявку в банк")
def submit(
    app_id: uuid.UUID,
    body: SubmitIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(client_only),
):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member)
    _require_status(app, AppStatus.DRAFT)
    _require_same_terms(db, app, body)
    problems = submit_problems(db, app)
    if problems:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": "Заявку нельзя отправить", "problems": problems}
        )
    app.submitted_at = utcnow()
    transition(
        db, app, AppStatus.SUBMITTED, actor_type=ActorType.CLIENT, actor_id=user.id, title="Заявка отправлена в банк"
    )
    transition(
        db,
        app,
        AppStatus.PRECHECK,
        title="Заявка принята в работу",
        stage_detail="Проверяем полноту и корректность данных",
    )
    enqueue(db, app, JobStage.PRECHECK)
    _commit(db)
    return _out(db, app, member)


@router.post("/{app_id}/info-response", response_model=ApplicationOut, summary="Ответить на запрос банка")
def info_response(
    app_id: uuid.UUID, body: InfoResponseIn, db: Session = Depends(get_db), user: User = Depends(client_only)
):
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
        changes = body.fields.model_dump(exclude_unset=True)
        if any(changes.get(key) for key in ("amount", "term_months")):
            # сумма и срок — условия, под которыми подписаны согласия и отправлена заявка
            _require_signer(member, "Изменить сумму или срок кредита", hint=False)
        for key, value in changes.items():
            if key in INFO_EDITABLE_FIELDS or (key in ("amount", "term_months") and value):
                setattr(app, key, value)
    # запрошенный документ засчитывается, только если его приложили после запроса банка
    have_new = {d.doc_type for d in app.documents if d.uploaded_at >= req.created_at}
    # сумма и срок — в пределах продукта, иначе проверка снова вернёт тот же запрос
    problems = terms_problems(app)
    for item in req.items:
        if item["type"] == "DOCUMENT" and item["doc_type"] not in have_new:
            problems.append(f"Приложите: {item['label']}")
        if item["type"] == "COMMENT" and not (body.comment or "").strip():
            problems.append(f"Ответьте: {item['label']}")
        if item["type"] == "FIELD":
            field = item.get("field")
            if field and not getattr(app, field, None):
                problems.append(f"Заполните: {item['label']}")
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {"message": "Ответ неполный", "problems": problems})
    req.status = InfoRequestStatus.ANSWERED
    req.answered_at = utcnow()
    req.answer_comment = body.comment
    if body.comment:
        db.add(Message(application_id=app.id, author_id=user.id, author_role=Role.CLIENT, text=body.comment))
    add_event(
        db, app, "INFO_PROVIDED", "Ответ на запрос банка отправлен", actor_type=ActorType.CLIENT, actor_id=user.id
    )
    transition(db, app, AppStatus.PRECHECK, title="Заявка снова в работе", stage_detail="Проверяем новые данные")
    enqueue(db, app, JobStage.PRECHECK)
    _commit(db)
    return _out(db, app, member)


@router.post("/{app_id}/cancel", response_model=ApplicationOut, summary="Отозвать заявку")
def cancel(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    if app.status not in CLIENT_CANCELLABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Заявку уже нельзя отозвать")
    if not can_cancel(app, member):
        _require_signer(member, "Отозвать заявку", hint=False)
    _withdraw(db, app, user)
    _commit(db)
    return _out(db, app, member)


def _withdraw(db: Session, app: Application, user: User, title: str | None = None) -> None:
    for req in db.scalars(
        select(InfoRequest).where(InfoRequest.application_id == app.id, InfoRequest.status == InfoRequestStatus.OPEN)
    ):
        req.status = InfoRequestStatus.CANCELLED
    title = title or ("Черновик отменён" if app.status == AppStatus.DRAFT else "Заявка отозвана клиентом")
    transition(db, app, AppStatus.CANCELLED, actor_type=ActorType.CLIENT, actor_id=user.id, title=title)


# --------------------------------------------------------------------------- предложение и подписание


@router.post("/{app_id}/offers/{offer_id}/accept", response_model=ApplicationOut, summary="Принять предложение")
def accept_offer(
    app_id: uuid.UUID, offer_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)
):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member, "Выбрать условия кредита")
    _require_status(app, AppStatus.OFFER_READY)
    offer = db.get(Offer, offer_id)
    if offer is None or offer.application_id != app.id or offer.status != OfferStatus.ACTIVE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предложение не найдено")
    if offer.valid_until < utcnow():
        raise HTTPException(status.HTTP_409_CONFLICT, "Срок действия предложения истёк")
    # Принятое предложение становится обязательством компании. Принятия по разным заявкам одной компании
    # идут по очереди, и каждое видит уже принятые: вместе они не обойдут ограничение долговой нагрузки
    lock_key(db, f"company-load:{app.company_id}")
    room = assessment.load_room(db, app, db.get(Decision, offer.decision_id))
    if room is not None and not room.fits(float(offer.monthly_payment)):
        raise HTTPException(status.HTTP_409_CONFLICT, room.refusal(float(offer.monthly_payment)))
    for o in db.scalars(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACTIVE)):
        o.status = OfferStatus.SUPERSEDED
    offer.status = OfferStatus.ACCEPTED
    offer.accepted_at = utcnow()
    transition(
        db,
        app,
        AppStatus.SIGNING,
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
        title=f"Выбрано предложение: {fmt_money(offer.amount)} на {offer.term_months} мес. под {fmt_rate(offer.rate)}",
    )
    _commit(db)
    return _out(db, app, member)


@router.post("/{app_id}/offers/decline", response_model=ApplicationOut, summary="Отказаться от предложения")
def decline_offers(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member, "Отказаться от предложения", hint=False)
    _require_status(app, AppStatus.OFFER_READY)
    if not _decline_offers(db, app, user):
        _commit(db)
        raise HTTPException(status.HTTP_409_CONFLICT, "Срок действия предложения уже истёк")
    _commit(db)
    return _out(db, app, member)


def _require_new_conditions(db: Session, parent: Application, body: ApplicationCreate, c: ReapplyIn) -> None:
    """Закрывать заявку ради такой же нет смысла: те же условия или вариант из действующего предложения."""
    product = db.get(Product, body.product_code)
    collateral = pledged(product, parent.has_collateral if c.has_collateral is None else c.has_collateral)
    guarantee = bool(parent.owner_guarantee if c.owner_guarantee is None else c.owner_guarantee)
    same_flags = collateral == pledged(product, parent.has_collateral) and guarantee == bool(parent.owner_guarantee)
    if (
        same_flags
        and body.product_code == parent.product_code
        and abs(body.amount - float(parent.amount)) < 0.5
        and body.term_months == parent.term_months
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Условия совпадают с текущей заявкой — измените их")
    if parent.status == AppStatus.OFFER_READY and same_flags:
        now = utcnow()
        for o in db.scalars(select(Offer).where(Offer.application_id == parent.id, Offer.status == OfferStatus.ACTIVE)):
            if (
                o.valid_until > now
                and o.product_code == body.product_code
                and o.term_months == body.term_months
                and abs(float(o.amount) - body.amount) < 0.5
            ):
                room = assessment.load_room(db, parent, db.get(Decision, o.decision_id))
                if room is not None and not room.fits(float(o.monthly_payment)):
                    return  # вариант уже не помещается в нагрузку: новая заявка рассчитает его заново
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Этот вариант есть в предложении банка — выберите его, новая заявка не нужна",
                )


def _decline_offers(db: Session, app: Application, user: User, title: str = "Отказ от предложения") -> bool:
    """Отказ от предложения. False — если оно уже истекло: заявка закрывается как истёкшая."""
    active = list(db.scalars(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACTIVE)))
    if active and all(o.valid_until < utcnow() for o in active):
        # так же, как при попытке принять: истёкшее предложение закрывается как истёкшее
        for o in active:
            o.status = OfferStatus.EXPIRED
        transition(db, app, AppStatus.EXPIRED, title="Срок действия предложения истёк")
        return False
    for o in active:
        o.status = OfferStatus.DECLINED
    transition(db, app, AppStatus.CLIENT_DECLINED, actor_type=ActorType.CLIENT, actor_id=user.id, title=title)
    return True


@router.post("/{app_id}/signing/back", response_model=ApplicationOut, summary="Вернуться к выбору условий")
def back_to_offers(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member, "Вернуться к выбору условий", hint=False)
    _require_status(app, AppStatus.SIGNING)
    accepted = db.scalar(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACCEPTED))
    if accepted is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Выбранное предложение не найдено. Обновите страницу")
    now = utcnow()
    reopened = 0
    for o in db.scalars(select(Offer).where(Offer.application_id == app.id, Offer.decision_id == accepted.decision_id)):
        if o.status not in (OfferStatus.ACCEPTED, OfferStatus.SUPERSEDED):
            continue
        if o.valid_until > now:
            o.status = OfferStatus.ACTIVE
            o.accepted_at = None
            reopened += 1
        else:
            o.status = OfferStatus.EXPIRED
    if not reopened:
        transition(db, app, AppStatus.EXPIRED, title="Срок действия предложения истёк")
        _commit(db)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Срок действия предложения истёк. Можно подать новую заявку с этими данными"
        )
    transition(
        db,
        app,
        AppStatus.OFFER_READY,
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
        title="Возврат к выбору условий",
    )
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
    loan = db.scalar(select(Loan).where(Loan.application_id == app.id))
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
        "schedule": loan.schedule if loan else schedule_for(offer),
        "documents": [
            {"code": "contract", "title": f"Кредитный договор № КД-{app.number}"},
            {"code": "schedule", "title": "График платежей (приложение 1 к договору)"},
            {"code": "disbursement", "title": "Заявление на выдачу кредита на расчётный счёт"},
        ],
        "signer": {"full_name": signer.user.full_name, "position_label": POSITION_LABELS.get(signer.position)}
        if signer
        else None,
    }


@router.post("/{app_id}/sign", response_model=ApplicationOut, summary="Подписать кредитный договор кодом")
def sign_contract(
    app_id: uuid.UUID, body: SignConfirmIn, db: Session = Depends(get_db), user: User = Depends(client_only)
):
    app, member = get_client_application(db, user, app_id)
    _require_signer(member)
    lock_key(db, f"sign:{app.id}")
    db.refresh(app)
    _require_status(app, AppStatus.SIGNING)
    offer = db.scalar(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACCEPTED))
    if offer is None or offer.valid_until < utcnow():
        # условия действуют ограниченное время: после срока договор по ним не заключается
        if offer is not None:
            offer.status = OfferStatus.EXPIRED
        transition(db, app, AppStatus.EXPIRED, title="Срок действия предложения истёк до подписания договора")
        _commit(db)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Срок действия предложения истёк. Можно подать новую заявку с этими данными"
        )
    _verify_challenge(db, app, user, SignPurpose.CONTRACT, body.challenge_id, body.code)
    offer.signed_at = utcnow()  # от даты подписания строится график кредита
    add_event(
        db,
        app,
        "CONTRACT_SIGNED",
        f"Кредитный договор подписан: {user.full_name}",
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
    )
    revolving = offer.product.repayment_type == "REVOLVING"
    transition(
        db,
        app,
        AppStatus.DISBURSEMENT,
        title="Договор подписан, устанавливаем лимит овердрафта"
        if revolving
        else "Договор подписан, перечисляем деньги",
        stage_detail="Готовим подключение овердрафта" if revolving else "Готовим выдачу кредита",
    )
    enqueue(db, app, JobStage.DISBURSEMENT)
    _commit(db)
    return _out(db, app, member)


# --------------------------------------------------------------------------- прочее


@router.post(
    "/{app_id}/reapply",
    response_model=ApplicationOut,
    status_code=201,
    summary="Подать новую заявку на основе этой (закрытой — или закрыть её и подать одним действием)",
)
def reapply(
    app_id: uuid.UUID,
    conditions: ReapplyIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(client_only),
):
    parent, parent_member = get_client_application(db, user, app_id)
    lock_key(db, f"reapply:{parent.id}")  # две одновременные кнопки не создадут две заявки
    db.refresh(parent)
    if conditions and conditions.expected_status and conditions.expected_status != parent.status:
        # пока клиент выбирал условия, заявка изменилась (например, специалист её одобрил) — ничего не закрываем
        title = CLIENT_STATUS[AppStatus(parent.status)][0]
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Заявка изменилась: сейчас она в статусе «{title}». Обновите страницу"
        )
    replace = bool(conditions and conditions.replace_current) and parent.status in REPLACEABLE
    if replace:
        # отказ от предложения и отзыв — те же права, что и у отдельных кнопок
        if parent.status == AppStatus.OFFER_READY:
            _require_signer(parent_member, "Отказаться от предложения", hint=False)
        elif not can_cancel(parent, parent_member):
            _require_signer(parent_member, "Отозвать заявку", hint=False)
    else:
        _require_status(parent, AppStatus.DECLINED, AppStatus.EXPIRED, AppStatus.CLIENT_DECLINED, AppStatus.CANCELLED)
    live = db.scalar(
        select(Application.number)
        .where(Application.parent_id == parent.id, Application.status.not_in([s.value for s in TERMINAL_STATUSES]))
        .limit(1)
    )
    if live:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"По этой заявке уже создана новая — № {live}. Откройте её в списке заявок"
        )
    c = conditions or ReapplyIn()
    if c.product_code:
        product = db.get(Product, c.product_code)
        if product is None or not product.is_active or parent.purpose not in product.purposes:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Продукт не подходит для цели заявки")
    body = ApplicationCreate(
        company_id=parent.company_id,
        product_code=c.product_code or parent.product_code,
        amount=c.amount if c.amount is not None else float(parent.amount),
        term_months=c.term_months or parent.term_months,
        purpose=parent.purpose,
        purpose_details=parent.purpose_details,
    )
    if conditions is not None:
        # условия из конструктора — в пределах продукта, иначе черновик потом не отправить
        product = db.get(Product, body.product_code)
        if product is not None and not float(product.min_amount) <= body.amount <= float(product.max_amount):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Сумма по продукту «{product.name}» — от {fmt_amount(product.min_amount)} до {fmt_money(product.max_amount)}",
            )
        if product is not None and not product.min_term <= body.term_months <= product.max_term:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Срок по продукту «{product.name}» — {months_range(product.min_term, product.max_term)}",
            )
    if replace:
        _require_new_conditions(db, parent, body, c)
        # текущая заявка закрывается в той же транзакции: без новой заявки она не закроется, и наоборот
        if parent.status == AppStatus.OFFER_READY:
            _decline_offers(db, parent, user, title="Отказ от предложения: клиент подаёт заявку на других условиях")
        else:
            _withdraw(db, parent, user, title="Заявка отозвана: клиент подаёт её на других условиях")
    app, member = _new_application(db, user, body, parent)
    # условия из конструктора: залог и поручительство, если клиент их выбрал
    if c.owner_guarantee is not None:
        app.owner_guarantee = c.owner_guarantee
    if c.has_collateral is not None:
        app.has_collateral = pledged(db.get(Product, app.product_code), c.has_collateral)
    # в истории исходной заявки — ссылка на новую
    add_event(
        db,
        parent,
        "REAPPLIED",
        f"Создана новая заявка № {app.number}"
        + (f": {fmt_money(app.amount)} на {app.term_months} мес." if conditions is not None else ""),
        actor_type=ActorType.CLIENT,
        actor_id=user.id,
        payload={"application_id": str(app.id), "number": app.number},
    )
    # Документы переносятся ссылкой на тот же файл: место в хранилище не расходуется, файл удаляется,
    # только когда на него не ссылается ни одна заявка. Согласия не переносятся — их подписывают заново
    copied = []
    for d in parent.documents[:MAX_DOCUMENTS_PER_APPLICATION]:
        if not storage.exists(d.storage_key):
            continue  # файла нет в хранилище — клиент приложит его заново
        db.add(
            ApplicationDocument(
                application_id=app.id,
                doc_type=d.doc_type,
                file_name=d.file_name,
                content_type=d.content_type,
                size_bytes=d.size_bytes,
                sha256=d.sha256,
                storage_key=d.storage_key,
                uploaded_by_id=user.id,
            )
        )
        copied.append(DOC_TYPES.get(d.doc_type, d.doc_type))
    if copied:
        add_event(
            db,
            app,
            "DOCUMENTS_COPIED",
            f"Перенесены документы из заявки № {parent.number}: {', '.join(copied)}",
            actor_type=ActorType.CLIENT,
            actor_id=user.id,
        )
    _commit(db)
    return _out(db, app, member)


@router.post(
    "/{app_id}/simulation",
    response_model=SimulationOut,
    summary="Конструктор условий: решение банка при другой сумме, сроке, залоге и поручительстве",
)
def simulation(app_id: uuid.UUID, body: SimulationIn, db: Session = Depends(get_db), user: User = Depends(client_only)):
    """Расчёт той же балльной моделью и политикой, что и решение по заявке, на сведениях из ЕГРЮЛ, ФНС и БКИ,
    полученных при её рассмотрении. Ничего не сохраняет."""
    app, _ = get_client_application(db, user, app_id)
    try:
        sim = Simulator(db, app)
        base = sim.base_params()
        params = sim.params(**body.model_dump(exclude={"with_suggestions"}))
        result = sim.run(params)
        suggestions = sim.suggestions(params, result) if body.with_suggestions else []
    except SimulationError as e:
        code = status.HTTP_409_CONFLICT if e.unavailable else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(code, str(e)) from e
    return {"base": base.to_dict(), "result": result, "suggestions": suggestions, "products": sim.product_options()}


@router.get("/{app_id}/messages", response_model=list[MessageOut], summary="Переписка с банком по заявке")
def get_messages(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    return [
        message_out(m) for m in db.scalars(select(Message).where(Message.application_id == app.id).order_by(Message.id))
    ]


@router.post("/{app_id}/messages", response_model=MessageOut, status_code=201, summary="Написать в банк по заявке")
def post_message(app_id: uuid.UUID, body: MessageIn, db: Session = Depends(get_db), user: User = Depends(client_only)):
    app, _ = get_client_application(db, user, app_id)
    if app.submitted_at is None:
        # черновик банк не видит — и переписку по нему тоже
        raise HTTPException(status.HTTP_409_CONFLICT, "Переписка с банком откроется после отправки заявки")
    m = Message(application_id=app.id, author_id=user.id, author_role=Role.CLIENT, text=body.text.strip())
    db.add(m)
    _commit(db)
    db.refresh(m)
    return message_out(m)
