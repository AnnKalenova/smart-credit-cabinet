"""Рабочее место банка: очередь заявок, досье, решение аналитика, мониторинг, параметры политики."""

import math
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, contains_eager, selectinload
from sqlalchemy.orm.exc import StaleDataError

from app.config import get_settings
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
    JobStatus,
    Role,
)
from app.domain.policy import POLICY_VERSION, ProductParams, _offer, price, rate_bounds, round_rate
from app.domain.scoring import months_between
from app.domain.text import fmt_amount, fmt_money, fmt_rate, lower_first, months_range
from app.integrations.client import ExternalSystems, IntegrationError
from app.models import (
    Application,
    ApplicationDocument,
    Company,
    Decision,
    InfoRequest,
    Message,
    PipelineJob,
    Product,
    Setting,
    User,
)
from app.schemas import BankApplicationRow, BankDecisionIn, FaultIn, MessageIn, MessageOut, SettingIn, SettingOut
from app.security import require_roles
from app.services import assessment, storage
from app.services.pipeline import create_offers
from app.services.views import (
    bank_application_out,
    bank_rows,
    dashboard,
    disbursement_failed,
    latest_decision,
    latest_scoring,
    message_out,
)
from app.services.workflow import (
    DEFAULT_SETTINGS,
    add_event,
    enqueue,
    key_rate,
    local_today,
    lock_key,
    notify_company,
    transition,
    utcnow,
)

router = APIRouter(prefix="/api/v1/bank", tags=["Банк: обработка заявок"])
settings = get_settings()
staff = require_roles(Role.ANALYST, Role.RISK_MANAGER)


def _get_app(db: Session, app_id: uuid.UUID) -> Application:
    """Банк видит только отправленные заявки: черновик (даже отменённый) — данные клиента без его согласий."""
    app = db.get(Application, app_id)
    if app is None or app.submitted_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return app


def _require_risk_manager(user: User, action: str) -> None:
    if user.role != Role.RISK_MANAGER:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{action} может только риск-менеджер")


def _require_demo() -> None:
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Недоступно")


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
    query = select(Application).join(Company).where(Application.submitted_at.is_not(None))
    if scope == "queue":
        # в очереди специалиста — ручное рассмотрение и выдачи, которые не прошли после всех попыток
        failed = select(PipelineJob.id).where(
            PipelineJob.application_id == Application.id,
            PipelineJob.stage == JobStage.DISBURSEMENT,
            PipelineJob.status == JobStatus.FAILED,
        )
        running = select(PipelineJob.id).where(
            PipelineJob.application_id == Application.id,
            PipelineJob.stage == JobStage.DISBURSEMENT,
            PipelineJob.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
        query = query.where(
            or_(
                Application.status == AppStatus.MANUAL_REVIEW,
                (Application.status == AppStatus.DISBURSEMENT) & failed.exists() & ~running.exists(),
            )
        )
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
                "min_rate": rate_bounds(params, kr)[0],
                "max_rate": rate_bounds(params, kr)[1],
            }
        )
    data["key_rate"] = kr
    return data


@router.post("/applications/{app_id}/assign", summary="Взять заявку в работу")
def assign(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    if app.status != AppStatus.MANUAL_REVIEW:
        raise HTTPException(status.HTTP_409_CONFLICT, "Взять в работу можно заявку на ручном рассмотрении")
    if app.assignee_id == user.id:
        return {"ok": True}
    if app.assignee_id is not None and user.role != Role.RISK_MANAGER:
        owner = db.get(User, app.assignee_id)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Заявку уже рассматривает {owner.full_name if owner else 'другой специалист'}. "
            "Передать её может риск-менеджер",
        )
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
    lock_key(db, f"rerun:{app.id}")  # двойное нажатие не поставит этап в очередь дважды
    if app.status == AppStatus.DISBURSEMENT:
        # Выдача не прошла после всех попыток (например, АБС долго недоступна): повторяем её.
        # Запрос в АБС идемпотентен (ключ — номер заявки), поэтому повтор не создаст второй кредит
        if not disbursement_failed(db, app):
            raise HTTPException(status.HTTP_409_CONFLICT, "Выдача ещё выполняется — повтор не нужен")
        app.stage_detail = "Повторяем выдачу кредита"
        add_event(
            db,
            app,
            "DISBURSEMENT_RETRY",
            f"Специалист повторил выдачу: {user.full_name}",
            actor_type=ActorType.ANALYST,
            actor_id=user.id,
            client_visible=False,
        )
        enqueue(db, app, JobStage.DISBURSEMENT, delay_seconds=0.5)
        _commit(db)
        return {"ok": True}
    if app.status != AppStatus.MANUAL_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Повтор доступен на ручном рассмотрении и при сбое выдачи кредита"
        )
    last = latest_decision(db, app.id)
    if last and (last.details or {}).get("stage") == JobStage.PRECHECK:
        # проверка полноты не завершилась из-за сбоя — начинаем с неё, а не со сбора сведений
        transition(
            db,
            app,
            AppStatus.PRECHECK,
            actor_type=ActorType.ANALYST,
            actor_id=user.id,
            title="Специалист запустил повторную автоматическую проверку",
            stage_detail="Повторно проверяем полноту данных",
        )
        enqueue(db, app, JobStage.PRECHECK, delay_seconds=0.5)
        _commit(db)
        return {"ok": True}
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
    if app.assignee_id not in (None, user.id) and user.role != Role.RISK_MANAGER:
        owner = db.get(User, app.assignee_id)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Заявку рассматривает {owner.full_name if owner else 'другой специалист'}: "
            "решение принимает этот специалист или риск-менеджер",
        )
    if app.assignee_id is None:
        app.assignee_id = user.id
    scoring = latest_scoring(db, app.id)
    if body.action == "APPROVE" and scoring is None:
        # без сведений из ЕГРЮЛ, ФНС и БКИ не проверены стоп-факторы и не рассчитан рейтинг
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Одобрить можно после проверки по ЕГРЮЛ, ФНС и БКИ. Нажмите «Повторить проверку», "
            "когда источники снова станут доступны",
        )
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
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Сумма по продукту «{params.name}» — от {fmt_amount(params.min_amount)} "
                    f"до {fmt_money(params.max_amount)}",
                )
            if not params.min_term <= o.term_months <= params.max_term:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Срок по продукту «{params.name}» — {months_range(params.min_term, params.max_term)}",
                )
            default_rate, pricing = price(params, grade, kr, app.has_collateral, app.owner_guarantee, tenure)
            rate = round_rate(o.rate) if o.rate else default_rate
            low, high = rate_bounds(params, kr)  # те же границы, что в подсказке аналитику
            if not low - 1e-9 <= rate <= high + 1e-9:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Ставка по продукту «{params.name}» — от {fmt_rate(low)} до {fmt_rate(high)}",
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
        # Ограничение нагрузки 40% действует и для решения специалиста: рекомендация могла быть рассчитана до того,
        # как у компании появились другие кредиты. Принятия предложений по компании идут по очереди — здесь тоже
        lock_key(db, f"company-load:{app.company_id}")
        load = assessment.current_load(db, app)
        for i, d in enumerate(drafts, start=1):
            if not load.fits(d.monthly_payment):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, load.analyst_refusal(i, d.monthly_payment))
        # Одобрение, если среди вариантов есть запрошенные условия; иначе — встречное предложение
        outcome = DecisionOutcome.APPROVE if any(not d.is_counter for d in drafts) else DecisionOutcome.COUNTER_OFFER
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
            details={
                "offers": [d.to_dict() for d in drafts],
                "key_rate": kr,
                "grade": grade,
                # нагрузка, с которой специалист принимал решение: при принятии предложения она перепроверяется
                "existing_monthly_payments": round(load.existing, 2),
            },
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
        reason = (body.reason or "").strip()
        if not reason:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Укажите причину отказа")
        # причина — в объяснении решения, сообщение специалиста — отдельно (в заявке и переписке)
        db.add(
            Decision(
                application_id=app.id,
                kind=DecisionKind.MANUAL,
                outcome=DecisionOutcome.DECLINE,
                reasons=[{"code": "manual_decline", "kind": "STOP", "text": reason, "client_text": reason}],
                details={},
                policy_version=POLICY_VERSION,
                decided_by_id=user.id,
                comment_internal=body.comment_internal,
                message_to_client=body.message_to_client,
            )
        )
        app.decided_at = utcnow()
        transition(
            db,
            app,
            AppStatus.DECLINED,
            actor_type=ActorType.ANALYST,
            actor_id=user.id,
            title=f"Отказ: {lower_first(reason)}",
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
    return storage.file_response(doc)


@router.get("/applications/{app_id}/messages", response_model=list[MessageOut], summary="Переписка с клиентом")
def get_messages(app_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    return [
        message_out(m) for m in db.scalars(select(Message).where(Message.application_id == app.id).order_by(Message.id))
    ]


@router.post("/applications/{app_id}/messages", response_model=MessageOut, status_code=201, summary="Написать клиенту")
def post_message(app_id: uuid.UUID, body: MessageIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    app = _get_app(db, app_id)
    m = Message(application_id=app.id, author_id=user.id, author_role=Role.ANALYST, text=body.text)
    db.add(m)
    notify_company(db, app, f"Сообщение по заявке № {app.number}", body.text[:200])
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


def _set(db: Session, key: str, value, user: User) -> Setting:
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value, description=DEFAULT_SETTINGS[key][1])
        db.add(row)
    row.value = value
    row.updated_by = user.full_name
    return row


@router.put("/settings/{key}", response_model=SettingOut, summary="Изменить параметр (риск-менеджер)")
def put_setting(key: str, body: SettingIn, db: Session = Depends(get_db), user: User = Depends(staff)):
    _require_risk_manager(user, "Менять параметры кредитной политики")
    if key not in DEFAULT_SETTINGS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Параметр не найден")
    if key == "key_rate":
        raw = body.value
        if isinstance(raw, bool) or not isinstance(raw, int | float | str):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Ставка должна быть числом")
        try:
            value = round(float(str(raw).replace(",", ".").strip()), 2)
        except (TypeError, ValueError, OverflowError) as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Ставка должна быть числом") from e
        if not math.isfinite(value) or not 0 < value < 50:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Ставка должна быть больше 0 и меньше 50%")
        previous = db.get(Setting, key)
        changed = previous is None or float(previous.value) != value
        row = _set(db, key, value, user)
        if changed:
            # подпись к ставке в кабинете клиента не должна остаться от прошлого значения
            _set(db, "key_rate_note", f"Установлена в банке {local_today().strftime('%d.%m.%Y')}", user)
    else:
        if not isinstance(body.value, str) or not body.value.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Укажите текст")
        if len(body.value.strip()) > 300:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Текст — не длиннее 300 символов")
        row = _set(db, key, body.value.strip(), user)
    db.commit()
    db.refresh(row)
    return {
        "key": key,
        "value": row.value,
        "description": row.description,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


@router.get("/integrations/faults", summary="Состояние симулятора внешних систем (демо-стенд)")
def get_faults(user: User = Depends(staff)):
    _require_demo()
    try:
        with ExternalSystems() as ext:
            return ext.control_faults()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Симулятор внешних систем недоступен") from e


@router.post("/integrations/faults", summary="Смоделировать сбой внешней системы (демо-стенд)")
def set_fault(body: FaultIn, user: User = Depends(staff)):
    _require_demo()
    try:
        with ExternalSystems() as ext:
            return ext.control_faults(body.model_dump())
    except (IntegrationError, Exception) as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Симулятор внешних систем недоступен") from e
