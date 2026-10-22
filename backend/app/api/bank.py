"""Рабочее место банка: очередь заявок, досье, решение аналитика, мониторинг, параметры политики."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, contains_eager, selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.db import get_db
from app.domain.enums import (
    DOC_TYPES,
    TERMINAL_STATUSES,
    ActorType,
    AppStatus,
    DecisionKind,
    DecisionOutcome,
    InfoRequestStatus,
    JobStage,
    Role,
)
from app.domain.policy import POLICY_VERSION, ProductParams, _offer, price
from app.domain.scoring import months_between
from app.integrations.client import ExternalSystems, IntegrationError
from app.models import Application, ApplicationDocument, Company, Decision, InfoRequest, Message, Product, Setting, User
from app.schemas import BankApplicationRow, BankDecisionIn, FaultIn, MessageIn, MessageOut, SettingIn, SettingOut
from app.security import require_roles
from app.services import storage
from app.services.pipeline import create_offers
from app.services.views import bank_application_out, bank_rows, dashboard, latest_scoring, message_out
from app.services.workflow import (
    DEFAULT_SETTINGS,
    add_event,
    enqueue,
    key_rate,
    local_today,
    notify_company,
    transition,
    utcnow,
)

router = APIRouter(prefix="/api/v1/bank", tags=["Банк: обработка заявок"])
staff = require_roles(Role.ANALYST, Role.RISK_MANAGER)
risk_only = require_roles(Role.RISK_MANAGER)


def _get_app(db: Session, app_id: uuid.UUID) -> Application:
    app = db.get(Application, app_id)
    if app is None or app.status == AppStatus.DRAFT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return app


def _commit(db: Session) -> None:
    try:
        db.commit()
    except StaleDataError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Заявка изменилась, обновите страницу") from e


@router.get("/applications", response_model=list[BankApplicationRow], summary="Очередь и реестр заявок")
def list_applications(
    scope: Literal["queue", "active", "all", "mine"] = "active",
    status_filter: str | None = Query(default=None, alias="status"),
    product: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(staff),
):
    query = select(Application).join(Company).where(Application.status != AppStatus.DRAFT)
    if scope == "queue":
        query = query.where(Application.status == AppStatus.MANUAL_REVIEW)
    elif scope == "active":
        query = query.where(Application.status.not_in([s.value for s in TERMINAL_STATUSES]))
    elif scope == "mine":
        query = query.where(Application.assignee_id == user.id)
    if status_filter:
        query = query.where(Application.status == status_filter)
    if product:
        query = query.where(Application.product_code == product)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(Application.number.ilike(like), Company.short_name.ilike(like), Company.inn.ilike(like))
        )
    if scope == "queue":
        query = query.order_by(Application.review_due_at.asc().nulls_last())
    else:
        query = query.order_by(Application.status_changed_at.desc())
    query = query.options(
        contains_eager(Application.company), selectinload(Application.product), selectinload(Application.assignee)
    )
    return bank_rows(db, list(db.scalars(query.limit(200))))


@router.get("/applications/{app_id}", summary="Досье заявки: данные источников, скоринг, решения, журнал")
def get_application(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    data = bank_application_out(db, app)
    scoring = latest_scoring(db, app.id)
    grade = scoring.grade if scoring else "C"
    kr = key_rate(db)
    tenure = months_between(app.company.client_since, local_today())
    data["pricing_hints"] = []
    for p in db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.sort_order)):
        params = ProductParams.from_orm(p)
        if app.purpose not in params.purposes:
            continue  # предложить можно только продукт под цель заявки — так же проверяет решение
        rate, _ = price(params, grade, kr, app.has_collateral, app.owner_guarantee, tenure)
        data["pricing_hints"].append(
            {
                "product_code": p.code,
                "product_name": p.name,
                "min_amount": params.min_amount,
                "max_amount": params.max_amount,
                "min_term": params.min_term,
                "max_term": params.max_term,
                "default_rate": rate,
                "min_rate": round(kr + 1.0, 1),
                "max_rate": round(kr + params.max_spread, 1),
            }
        )
    data["key_rate"] = kr
    return data


@router.post("/applications/{app_id}/assign", summary="Взять заявку в работу")
def assign(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    app.assignee_id = user.id
    add_event(
        db,
        app,
        "ASSIGNED",
        f"Заявку взял в работу: {user.full_name}",
        actor_type=ActorType.ANALYST,
        actor_id=user.id,
        client_visible=False,
    )
    _commit(db)
    return {"ok": True}


@router.post(
    "/applications/{app_id}/rerun", summary="Повторить автоматическую проверку (например, после сбоя внешнего сервиса)"
)
def rerun(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    if app.status != AppStatus.MANUAL_REVIEW:
        raise HTTPException(status.HTTP_409_CONFLICT, "Повтор доступен только на ручном рассмотрении")
    transition(
        db,
        app,
        AppStatus.ENRICHMENT,
        actor_type=ActorType.ANALYST,
        actor_id=user.id,
        title="Специалист запустил повторную автоматическую проверку",
        stage_detail="Повторно запрашиваем сведения о компании",
    )
    enqueue(db, app, JobStage.ENRICHMENT, delay_seconds=0.5)
    _commit(db)
    return {"ok": True}


@router.post("/applications/{app_id}/decision", summary="Решение кредитного специалиста")
def decide(app_id: uuid.UUID, body: BankDecisionIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    if app.status != AppStatus.MANUAL_REVIEW:
        raise HTTPException(status.HTTP_409_CONFLICT, "Решение принимается только по заявкам на ручном рассмотрении")
    if app.assignee_id is None:
        app.assignee_id = user.id
    scoring = latest_scoring(db, app.id)
    grade = scoring.grade if scoring else "C"
    kr = key_rate(db)
    tenure = months_between(app.company.client_since, local_today())

    if body.action == "APPROVE":
        if not body.offers:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Укажите условия предложения")
        drafts = []
        for o in body.offers:
            product = db.get(Product, o.product_code)
            if product is None or not product.is_active:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Продукт {o.product_code} не найден")
            params = ProductParams.from_orm(product)
            if app.purpose not in params.purposes:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, f"Продукт «{params.name}» не подходит для цели заявки"
                )
            if not params.min_amount <= o.amount <= params.max_amount:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, f"Сумма вне условий продукта «{params.name}»"
                )
            if not params.min_term <= o.term_months <= params.max_term:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Срок вне условий продукта «{params.name}»")
            default_rate, pricing = price(params, grade, kr, app.has_collateral, app.owner_guarantee, tenure)
            rate = round(o.rate, 1) if o.rate else default_rate
            if not kr + 1.0 - 1e-9 <= rate <= kr + params.max_spread + 1e-9:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Ставка должна быть от {kr + 1.0:.1f}% до {kr + params.max_spread:.1f}%",
                )
            if rate != default_rate:
                pricing = [
                    *pricing,
                    {"label": "Корректировка кредитного специалиста", "value": round(rate - default_rate, 2)},
                ]
            same = (
                o.product_code == app.product_code
                and abs(o.amount - float(app.amount)) < 1
                and o.term_months == app.term_months
            )
            label = o.label or ("Запрошенные условия" if same else "Условия, согласованные специалистом")
            draft = _offer(
                params, o.amount, o.term_months, rate, pricing, app.owner_guarantee, not same, label, app.has_collateral
            )
            drafts.append(draft)
        outcome = (
            DecisionOutcome.APPROVE if len(drafts) == 1 and not drafts[0].is_counter else DecisionOutcome.COUNTER_OFFER
        )
        decision = Decision(
            id=uuid.uuid4(),
            application_id=app.id,
            kind=DecisionKind.MANUAL,
            outcome=outcome,
            reasons=[
                {
                    "code": "manual_approve",
                    "kind": "NOTE",
                    "text": body.comment_internal or "Одобрено кредитным специалистом",
                    "client_text": body.message_to_client or "",
                }
            ],
            details={"offers": [d.to_dict() for d in drafts], "key_rate": kr, "grade": grade},
            policy_version=POLICY_VERSION,
            decided_by_id=user.id,
            comment_internal=body.comment_internal,
            message_to_client=body.message_to_client,
        )
        db.add(decision)
        db.flush()
        create_offers(db, app, decision, drafts)
        app.decided_at = utcnow()
        title = (
            "Специалист одобрил заявку — готово предложение"
            if outcome == DecisionOutcome.APPROVE
            else "Специалист подготовил предложение"
        )
        transition(db, app, AppStatus.OFFER_READY, actor_type=ActorType.ANALYST, actor_id=user.id, title=title)

    elif body.action == "REQUEST_INFO":
        if not body.info_items:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Укажите, что нужно предоставить")
        items = []
        for i, item in enumerate(body.info_items):
            if item.type == "DOCUMENT" and item.doc_type not in DOC_TYPES:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неизвестный тип документа")
            items.append({"code": f"analyst_{i}", "type": item.type, "doc_type": item.doc_type, "label": item.label})
        message = body.message_to_client or "Кредитному специалисту нужна дополнительная информация по заявке."
        db.add(
            InfoRequest(
                application_id=app.id,
                requested_by_id=user.id,
                items=items,
                message=message,
                status=InfoRequestStatus.OPEN,
            )
        )
        db.add(
            Decision(
                application_id=app.id,
                kind=DecisionKind.MANUAL,
                outcome=DecisionOutcome.NEEDS_INFO,
                reasons=[
                    {
                        "code": "manual_info",
                        "kind": "INFO",
                        "text": body.comment_internal or "Запрошена информация",
                        "client_text": message,
                    }
                ],
                details={"items": items},
                policy_version=POLICY_VERSION,
                decided_by_id=user.id,
                comment_internal=body.comment_internal,
                message_to_client=message,
            )
        )
        transition(
            db,
            app,
            AppStatus.NEEDS_INFO,
            actor_type=ActorType.ANALYST,
            actor_id=user.id,
            title="Специалист запросил дополнительную информацию",
        )

    else:  # DECLINE
        if not (body.reason or "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Укажите причину отказа")
        client_text = body.message_to_client or body.reason
        db.add(
            Decision(
                application_id=app.id,
                kind=DecisionKind.MANUAL,
                outcome=DecisionOutcome.DECLINE,
                reasons=[{"code": "manual_decline", "kind": "STOP", "text": body.reason, "client_text": client_text}],
                details={},
                policy_version=POLICY_VERSION,
                decided_by_id=user.id,
                comment_internal=body.comment_internal,
                message_to_client=client_text,
            )
        )
        app.decided_at = utcnow()
        transition(
            db, app, AppStatus.DECLINED, actor_type=ActorType.ANALYST, actor_id=user.id, title=f"Отказ: {client_text}"
        )

    if body.message_to_client:
        db.add(Message(application_id=app.id, author_id=user.id, author_role=Role.ANALYST, text=body.message_to_client))
    _commit(db)
    return {"ok": True, "status": app.status}


@router.get("/applications/{app_id}/documents/{doc_id}", summary="Скачать документ клиента")
def download_document(app_id: uuid.UUID, doc_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    doc = db.get(ApplicationDocument, doc_id)
    if doc is None or doc.application_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    return FileResponse(storage.path_for(doc.storage_key), media_type=doc.content_type, filename=doc.file_name)


@router.get("/applications/{app_id}/messages", response_model=list[MessageOut], summary="Переписка с клиентом")
def get_messages(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    return [
        message_out(m) for m in db.scalars(select(Message).where(Message.application_id == app.id).order_by(Message.id))
    ]


@router.post("/applications/{app_id}/messages", response_model=MessageOut, status_code=201, summary="Написать клиенту")
def post_message(app_id: uuid.UUID, body: MessageIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    m = Message(application_id=app.id, author_id=user.id, author_role=Role.ANALYST, text=body.text.strip())
    db.add(m)
    notify_company(db, app, f"Сообщение по заявке № {app.number}", body.text.strip()[:200])
    _commit(db)
    db.refresh(m)
    return message_out(m)


@router.get("/dashboard", summary="Мониторинг конвейера: воронка, очередь, SLA, интеграции")
def get_dashboard(db: Session = Depends(get_db), user: User = Depends(staff)):
    return dashboard(db)


@router.get("/settings", response_model=list[SettingOut], summary="Параметры кредитной политики")
def get_settings_list(db: Session = Depends(get_db), user: User = Depends(staff)):
    result = []
    for key, (default, description) in DEFAULT_SETTINGS.items():
        row = db.get(Setting, key)
        result.append(
            {
                "key": key,
                "value": row.value if row else default,
                "description": description,
                "updated_at": row.updated_at if row else None,
                "updated_by": row.updated_by if row else None,
            }
        )
    return result


@router.put("/settings/{key}", response_model=SettingOut, summary="Изменить параметр (риск-менеджер)")
def put_setting(key: str, body: SettingIn, db: Session = Depends(get_db), user: User = Depends(risk_only)):
    if key not in DEFAULT_SETTINGS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Параметр не найден")
    if key == "key_rate":
        try:
            value = round(float(body.value), 2)
        except (TypeError, ValueError) as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Ставка должна быть числом") from e
        if not 0 < value < 50:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Ставка должна быть от 0 до 50%")
    else:
        value = str(body.value)[:300]
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value, description=DEFAULT_SETTINGS[key][1])
        db.add(row)
    row.value = value
    row.updated_by = user.full_name
    db.commit()
    db.refresh(row)
    return {
        "key": key,
        "value": row.value,
        "description": row.description,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


@router.get("/integrations/faults", summary="Состояние симулятора внешних систем")
def get_faults(user: User = Depends(staff)):
    try:
        with ExternalSystems() as ext:
            return ext.control_faults()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Симулятор внешних систем недоступен") from e


@router.post("/integrations/faults", summary="Смоделировать сбой внешней системы (демонстрация отказоустойчивости)")
def set_fault(body: FaultIn, user: User = Depends(staff)):
    try:
        with ExternalSystems() as ext:
            return ext.control_faults(body.model_dump())
    except (IntegrationError, Exception) as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Симулятор внешних систем недоступен") from e
