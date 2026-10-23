"""Сборка ответов API из ORM-объектов: отдельно для клиента и для банка."""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.enums import (
    DECISION_LABELS,
    DOC_TYPES,
    POSITION_LABELS,
    TERMINAL_STATUSES,
    AppStatus,
    DecisionOutcome,
    InfoRequestStatus,
    JobStage,
    JobStatus,
    OfferStatus,
    Role,
)
from app.domain.finance import build_annuity_schedule, build_revolving_schedule
from app.domain.products import required_documents
from app.domain.scoring import GRADE_LABELS
from app.domain.statuses import BANK_LABELS, CLIENT_CANCELLABLE, CLIENT_STATUS, client_view
from app.models import (
    Application,
    ApplicationEvent,
    CheckResult,
    Company,
    CompanyMember,
    Consent,
    Decision,
    EnrichmentResult,
    InfoRequest,
    IntegrationCall,
    Loan,
    Message,
    Offer,
    PipelineJob,
    ScoringResult,
    User,
)
from app.services import assessment
from app.services.workflow import local_today, utcnow


def local_time(dt):
    """Время в часовом поясе клиента (по умолчанию московское) — сервер в контейнере живёт в UTC."""
    return dt.astimezone(ZoneInfo(get_settings().display_timezone))


def product_out(p) -> dict:
    return {
        "code": p.code,
        "name": p.name,
        "tagline": p.tagline,
        "description": p.description,
        "purposes": list(p.purposes),
        "min_amount": float(p.min_amount),
        "max_amount": float(p.max_amount),
        "min_term": p.min_term,
        "max_term": p.max_term,
        "base_spread": float(p.base_spread),
        "repayment_type": p.repayment_type,
        "collateral": p.collateral,
        "features": list(p.features),
        "auto_decision_limit": float(p.auto_decision_limit),
    }


def document_out(d) -> dict:
    return {
        "id": d.id,
        "doc_type": d.doc_type,
        "doc_type_label": DOC_TYPES.get(d.doc_type, d.doc_type),
        "file_name": d.file_name,
        "content_type": d.content_type,
        "size_bytes": d.size_bytes,
        "uploaded_at": d.uploaded_at,
    }


def schedule_start(offer) -> date:
    """Дата начала графика: день подписания договора, а до подписания — сегодня. Выдача строит график
    от той же даты, поэтому подписанный график и график выданного кредита совпадают."""
    if offer.signed_at is not None:
        return local_time(offer.signed_at).date()
    return local_today()


def schedule_for(offer) -> list[dict]:
    """График платежей по предложению (после выдачи показывается сохранённый график кредита)."""
    start = schedule_start(offer)
    if offer.product.repayment_type == "REVOLVING":
        return build_revolving_schedule(float(offer.amount), float(offer.rate), offer.term_months, start)
    return build_annuity_schedule(float(offer.amount), float(offer.rate), offer.term_months, start)


def offer_out(o: Offer, full_schedule: bool = False, unavailable_reason: str | None = None) -> dict:
    schedule = schedule_for(o)
    return {
        "id": o.id,
        "product_code": o.product_code,
        "product_name": o.product.name,
        "repayment_type": o.product.repayment_type,
        "amount": float(o.amount),
        "term_months": o.term_months,
        "rate": float(o.rate),
        "monthly_payment": float(o.monthly_payment),
        "total_interest": float(o.total_interest),
        "conditions": o.conditions,
        "pricing": o.pricing,
        "is_counter": o.is_counter,
        "label": o.label,
        "status": o.status,
        "valid_until": o.valid_until,
        "schedule_preview": schedule if full_schedule else schedule[:6],
        # вариант больше не помещается в ограничение нагрузки: после решения у компании появились кредиты
        "unavailable_reason": unavailable_reason,
    }


def offers_out(db: Session, app: Application, offers: list[Offer]) -> list[dict]:
    """Варианты предложения. Пока их можно принять, каждый сверяется с текущей нагрузкой компании — так же,
    как при принятии: клиент заранее видит, что вариант не подойдёт, и почему."""
    room = None
    if app.status == AppStatus.OFFER_READY and offers:
        room = assessment.load_room(db, app, db.get(Decision, offers[0].decision_id))
    out = []
    for o in offers:
        payment = float(o.monthly_payment)
        reason = room.refusal(payment) if room is not None and not room.fits(payment) else None
        out.append(offer_out(o, unavailable_reason=reason))
    return out


def event_out(e: ApplicationEvent) -> dict:
    return {
        "id": e.id,
        "created_at": e.created_at,
        "event_type": e.event_type,
        "title": e.title,
        "actor_type": e.actor_type,
        "actor_name": e.actor.full_name if e.actor else None,
        "from_status": e.from_status,
        "to_status": e.to_status,
    }


def info_request_out(r: InfoRequest | None, db: Session) -> dict | None:
    if r is None:
        return None
    requester = db.get(User, r.requested_by_id) if r.requested_by_id else None
    return {
        "id": r.id,
        "created_at": r.created_at,
        "items": r.items,
        "message": r.message,
        "status": r.status,
        "answered_at": r.answered_at,
        "answer_comment": r.answer_comment,
        "requested_by": requester.full_name if requester else "Автоматическая проверка",
    }


def loan_out(loan: Loan, app: Application, with_schedule: bool = False) -> dict:
    return {
        "id": loan.id,
        "application_id": loan.application_id,
        "application_number": app.number,
        "company_name": app.company.short_name,
        "product_code": loan.product_code,
        "product_name": loan.product.name,
        "contract_number": loan.contract_number,
        "loan_account": loan.loan_account,
        "amount": float(loan.amount),
        "term_months": loan.term_months,
        "rate": float(loan.rate),
        "monthly_payment": float(loan.monthly_payment),
        "issued_at": loan.issued_at,
        "first_payment_date": loan.first_payment_date,
        "status": loan.status,
        "repayment_type": loan.product.repayment_type,
        "schedule": loan.schedule if with_schedule else None,
    }


def app_status_view(app: Application) -> dict:
    """Статус для клиента с поправкой на продукт и историю заявки (без запросов к БД)."""
    view = client_view(app.status)
    status = AppStatus(app.status)
    revolving = app.product.repayment_type == "REVOLVING"
    if status == AppStatus.DISBURSEMENT and revolving:
        view["title"] = "Подключаем овердрафт"
        view["now"] = "Устанавливаем лимит овердрафта на ваш расчётный счёт."
    elif status == AppStatus.ISSUED and revolving:
        view["title"] = "Овердрафт подключён"
        view["now"] = "Лимит овердрафта установлен на расчётный счёт: деньги доступны при нехватке остатка."
    elif status == AppStatus.CANCELLED and app.submitted_at is None:
        view["title"] = "Черновик отменён"
        view["now"] = "Заявка не отправлялась в банк."
        view["step"] = 0  # рассмотрение не начиналось
    return view


def summary_out(app: Application) -> dict:
    view = app_status_view(app)
    return {
        "id": app.id,
        "number": app.number,
        "company_id": app.company_id,
        "company_name": app.company.short_name,
        "product_code": app.product_code,
        "product_name": app.product.name,
        "amount": float(app.amount),
        "term_months": app.term_months,
        "purpose": app.purpose,
        "status": app.status,
        "status_view": view,
        "stage_detail": app.stage_detail,
        "created_at": app.created_at,
        "status_changed_at": app.status_changed_at,
        "submitted_at": app.submitted_at,
    }


def latest_decision(db: Session, app_id) -> Decision | None:
    return db.scalar(select(Decision).where(Decision.application_id == app_id).order_by(Decision.created_at.desc()))


def latest_scoring(db: Session, app_id) -> ScoringResult | None:
    return db.scalar(
        select(ScoringResult).where(ScoringResult.application_id == app_id).order_by(ScoringResult.id.desc())
    )


def visible_offers(db: Session, app: Application) -> list[Offer]:
    status_map = {
        AppStatus.OFFER_READY: [OfferStatus.ACTIVE],
        AppStatus.SIGNING: [OfferStatus.ACCEPTED],
        AppStatus.DISBURSEMENT: [OfferStatus.ACCEPTED],
        AppStatus.ISSUED: [OfferStatus.ACCEPTED],
        AppStatus.EXPIRED: [OfferStatus.EXPIRED],
        AppStatus.CLIENT_DECLINED: [OfferStatus.DECLINED],
    }
    wanted = status_map.get(AppStatus(app.status))
    if not wanted:
        return []
    return list(
        db.scalars(
            select(Offer)
            .where(Offer.application_id == app.id, Offer.status.in_(wanted))
            .order_by(Offer.is_counter, Offer.amount.desc())
        )
    )


def client_decision_view(db: Session, app: Application) -> dict | None:
    """Объяснение решения для клиента: без внутренних деталей, но с понятными причинами."""
    if app.status not in (
        AppStatus.OFFER_READY,
        AppStatus.SIGNING,
        AppStatus.DISBURSEMENT,
        AppStatus.ISSUED,
        AppStatus.DECLINED,
        AppStatus.EXPIRED,
        AppStatus.CLIENT_DECLINED,
    ):
        return None
    d = latest_decision(db, app.id)
    if d is None:
        return None
    reasons = [
        r["client_text"] for r in d.reasons if r.get("client_text") and r.get("kind") in ("STOP", "LIMIT", "INFO")
    ]
    scoring = latest_scoring(db, app.id)
    declined = d.outcome == DecisionOutcome.DECLINE
    if declined:
        scoring = None  # при отказе не показываем «минимальный риск» и сильные стороны — это сбивает с толку
    positives, negatives = [], []
    if scoring:
        for f in sorted(scoring.factors, key=lambda f: -f["points"]):
            if f["points"] > 0 and f.get("client_text") and len(positives) < 3:
                positives.append(f["client_text"])
        for f in sorted(scoring.factors, key=lambda f: f["points"]):
            if f["points"] < 0 and f.get("client_text") and len(negatives) < 3:
                negatives.append(f["client_text"])
    return {
        "outcome": d.outcome,
        "outcome_label": DECISION_LABELS[DecisionOutcome(d.outcome)],
        "kind": d.kind,
        "kind_label": "Автоматическое решение" if d.kind == "AUTO" else "Решение кредитного специалиста",
        "decided_at": d.created_at,
        "reasons": reasons,
        "positives": positives,
        "negatives": negatives,
        "message": d.message_to_client,
        "grade": scoring.grade if scoring else None,
        "grade_label": GRADE_LABELS.get(scoring.grade) if scoring else None,
    }


def local_deadline(dt) -> str:
    """«02.11.2026 09:57 МСК»: срок с точностью до минуты и с часовым поясом."""
    text = local_time(dt).strftime("%d.%m.%Y %H:%M")
    return text + " МСК" if get_settings().display_timezone == "Europe/Moscow" else text


def disbursement_failed(db: Session, app: Application) -> bool:
    """Выдача не прошла после всех попыток, и новой попытки в очереди нет — нужен повтор специалистом."""
    if app.status != AppStatus.DISBURSEMENT:
        return False
    last = db.scalar(
        select(PipelineJob.status)
        .where(PipelineJob.application_id == app.id, PipelineJob.stage == JobStage.DISBURSEMENT)
        .order_by(PipelineJob.id.desc())
        .limit(1)
    )
    return last == JobStatus.FAILED


def client_status_view(db: Session, app: Application, offers: list[Offer], member: CompanyMember | None = None) -> dict:
    view = app_status_view(app)
    status = AppStatus(app.status)
    sole = app.company.kind == "IP"
    signer_word = "предприниматель" if sole else "руководитель"
    can_sign = member is None or member.can_sign

    if status == AppStatus.DRAFT and not can_sign:
        view["next"] = (
            f"Заполните заявку и передайте её на подпись: согласия подписывает и заявку отправляет {signer_word} "
            "или представитель по доверенности."
        )
    elif status == AppStatus.ENRICHMENT:
        view["now"] = (
            "Запрашиваем сведения о вас в ЕГРИП, ФНС и бюро кредитных историй."
            if sole
            else "Запрашиваем сведения о компании в ЕГРЮЛ, ФНС и бюро кредитных историй."
        )
    elif status == AppStatus.SCORING and sole:
        view["now"] = "Рассчитываем кредитный рейтинг по данным о бизнесе и оборотах по счёту."
    elif status == AppStatus.MANUAL_REVIEW and app.review_due_at:
        if app.review_due_at < utcnow():
            view["next"] = (
                "Рассмотрение занимает больше времени, чем обычно. Решение появится здесь же, в кабинете, "
                "а мы пришлём уведомление."
            )
        else:
            view["next"] = (
                "Решение появится здесь же, в кабинете, ориентировочно до "
                f"{local_deadline(app.review_due_at)}. Если понадобятся документы, мы напишем в этой заявке."
            )
    elif status == AppStatus.OFFER_READY and offers:
        until = local_deadline(min(o.valid_until for o in offers))
        view["next"] = (
            f"Выберите вариант и примите предложение до {until}."
            if can_sign
            else f"Принять предложение может {signer_word} или представитель по доверенности — до {until}. "
            "Нажмите «Передать на подпись», и мы его уведомим."
        )
    elif status == AppStatus.SIGNING:
        view["next"] = (
            f"Подпишите документы кодом подтверждения. Подписывает {signer_word} или представитель по доверенности."
            if can_sign
            else f"Договор подписывает {signer_word} или представитель по доверенности. "
            "Нажмите «Передать на подпись», и мы его уведомим."
        )
    elif status == AppStatus.DISBURSEMENT and disbursement_failed(db, app):
        view["now"] = "Выдача задерживается: учётная система банка не ответила вовремя."
        view["next"] = "Заявка — в очереди специалиста банка, он повторит выдачу. От вас ничего не требуется."
    elif status == AppStatus.DECLINED:
        d = latest_decision(db, app.id)
        fixable = d is not None and not any(
            r.get("kind") == "STOP" and r.get("code") != "no_capacity" for r in d.reasons
        )
        if not fixable:
            view["now"] = "Банк не может выдать кредит."
    return view


def next_application(db: Session, app: Application) -> dict | None:
    """Новая заявка, поданная на основе этой (повторная подача или конструктор условий)."""
    child = db.scalar(
        select(Application).where(Application.parent_id == app.id).order_by(Application.created_at.desc()).limit(1)
    )
    if child is None:
        return None
    return {
        "id": child.id,
        "number": child.number,
        "status": child.status,
        "status_title": CLIENT_STATUS[AppStatus(child.status)][0],
        "is_live": AppStatus(child.status) not in TERMINAL_STATUSES,
    }


# где переданные руководителю условия ещё можно подать: предложение действует, заявка у специалиста
# или закрыта без выдачи (истёк срок, отказ от предложения, отзыв) — конструктор там доступен
PROPOSAL_STATUSES = {
    AppStatus.OFFER_READY,
    AppStatus.MANUAL_REVIEW,
    AppStatus.EXPIRED,
    AppStatus.CLIENT_DECLINED,
    AppStatus.CANCELLED,
}


def proposed_conditions(db: Session, app: Application) -> dict | None:
    """Условия из конструктора, которые сотрудник без права подписи передал руководителю, — пока по ним
    не подана новая заявка и заявку не перевели к подписанию договора."""
    if AppStatus(app.status) not in PROPOSAL_STATUSES:
        return None
    ev = db.scalar(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == app.id, ApplicationEvent.event_type == "CONDITIONS_PROPOSED")
        .order_by(ApplicationEvent.created_at.desc(), ApplicationEvent.id.desc())
        .limit(1)
    )
    if ev is None:
        return None
    newer_child = db.scalar(
        select(Application.id).where(Application.parent_id == app.id, Application.created_at > ev.created_at).limit(1)
    )
    if newer_child is not None:
        return None
    return {
        "conditions": ev.payload.get("conditions"),
        "text": ev.payload.get("text"),
        "by": ev.actor.full_name if ev.actor else None,
        "at": ev.created_at,
    }


def application_out(db: Session, app: Application, member: CompanyMember) -> dict:
    product = app.product
    offers = visible_offers(db, app)
    have = {d.doc_type for d in app.documents}
    req = [
        {"doc_type": t, "label": DOC_TYPES[t], "attached": t in have}
        for t in required_documents(product, float(app.amount), app.purpose, app.company.kind)
    ]
    consents = [
        {
            "type": c.consent_type,
            "text": c.text,
            "signed_at": c.signed_at,
            "signed_by": db.get(User, c.signed_by_id).full_name,
        }
        for c in db.scalars(select(Consent).where(Consent.application_id == app.id).order_by(Consent.signed_at))
    ]
    info = db.scalar(
        select(InfoRequest).where(InfoRequest.application_id == app.id).order_by(InfoRequest.created_at.desc())
    )
    events = db.scalars(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == app.id, ApplicationEvent.client_visible.is_(True))
        .order_by(ApplicationEvent.created_at, ApplicationEvent.id)
    )
    loan = db.scalar(select(Loan).where(Loan.application_id == app.id))
    signers = [
        m.user.full_name
        for m in db.scalars(
            select(CompanyMember).where(CompanyMember.company_id == app.company_id, CompanyMember.can_sign.is_(True))
        )
    ]
    data = summary_out(app)
    data["status_view"] = client_status_view(db, app, offers, member)
    child = next_application(db, app)
    data.update(
        {
            "purpose_details": app.purpose_details,
            "declared_revenue": float(app.declared_revenue) if app.declared_revenue is not None else None,
            "has_collateral": app.has_collateral,
            "collateral_description": app.collateral_description,
            "owner_guarantee": app.owner_guarantee,
            "contact_name": app.contact_name,
            "contact_phone": app.contact_phone,
            "product": product_out(product),
            "required_documents": req,
            "documents": [document_out(d) for d in app.documents],
            "consents": consents,
            "info_request": info_request_out(info, db)
            if info and (info.status == InfoRequestStatus.OPEN or app.status != AppStatus.DRAFT)
            else None,
            "offers": offers_out(db, app, offers),
            "decision": client_decision_view(db, app),
            "events": [event_out(e) for e in events],
            "loan": loan_out(loan, app, with_schedule=False) if loan else None,
            "review_due_at": app.review_due_at,
            "parent_id": app.parent_id,
            "next_application": child,
            "proposed_conditions": proposed_conditions(db, app),
            "permissions": {
                "can_edit": app.status == AppStatus.DRAFT,
                "can_submit": app.status == AppStatus.DRAFT and member.can_sign,
                "can_sign": member.can_sign,
                "can_cancel": can_cancel(app, member),
                "can_answer": app.status == AppStatus.NEEDS_INFO,
                "can_accept": app.status == AppStatus.OFFER_READY and member.can_sign,
                "can_request_signature": app.status in (AppStatus.DRAFT, AppStatus.OFFER_READY, AppStatus.SIGNING)
                and not member.can_sign,
                "can_reapply": app.status
                in (AppStatus.DECLINED, AppStatus.EXPIRED, AppStatus.CLIENT_DECLINED, AppStatus.CANCELLED)
                and not (child and child["is_live"]),
                "signers": signers,
                "position_label": POSITION_LABELS.get(member.position, member.position),
            },
        }
    )
    return data


# --------------------------------------------------------------------------- банк


def _triggers(d: Decision | None) -> list[str]:
    if d is None or d.outcome != DecisionOutcome.MANUAL_REVIEW:
        return []
    return [r["text"] for r in d.reasons if r.get("kind") == "MANUAL"]


def can_cancel(app: Application, member: CompanyMember) -> bool:
    """Отозвать отправленную заявку может подписант; свой черновик — и его автор."""
    if app.status not in CLIENT_CANCELLABLE:
        return False
    return member.can_sign or (app.status == AppStatus.DRAFT and app.created_by_id == member.user_id)


_UNSET = object()


def bank_rows(db: Session, apps: list[Application]) -> list[dict]:
    """Строки реестра без N+1: последние скоринг и решение по всем заявкам — двумя запросами."""
    ids = [a.id for a in apps]
    if not ids:
        return []
    scorings: dict = {}
    for r in db.scalars(select(ScoringResult).where(ScoringResult.application_id.in_(ids)).order_by(ScoringResult.id)):
        scorings[r.application_id] = r  # остаётся последний по порядку
    decisions: dict = {}
    for d in db.scalars(
        select(Decision).where(Decision.application_id.in_(ids)).order_by(Decision.created_at, Decision.id)
    ):
        decisions[d.application_id] = d
    # последнее сообщение по каждой заявке: если писал клиент — он ждёт ответа
    last_authors = dict(
        db.execute(
            select(Message.application_id, Message.author_role)
            .where(Message.application_id.in_(ids))
            .order_by(Message.application_id, Message.id.desc())
            .distinct(Message.application_id)
        ).all()
    )
    return [
        bank_row(db, a, scorings.get(a.id), decisions.get(a.id), last_authors.get(a.id) == Role.CLIENT) for a in apps
    ]


def awaiting_reply(db: Session, app_id) -> bool:
    last = db.scalar(
        select(Message.author_role).where(Message.application_id == app_id).order_by(Message.id.desc()).limit(1)
    )
    return last == Role.CLIENT


def bank_row(db: Session, app: Application, scoring=_UNSET, d=_UNSET, waiting=_UNSET) -> dict:
    if scoring is _UNSET:
        scoring = latest_scoring(db, app.id)
    if d is _UNSET:
        d = latest_decision(db, app.id)
    if waiting is _UNSET:
        waiting = awaiting_reply(db, app.id)
    now = utcnow()
    return {
        "id": app.id,
        "number": app.number,
        "company_name": app.company.short_name,
        "company_inn": app.company.inn,
        "company_kind": app.company.kind,
        "product_code": app.product_code,
        "product_name": app.product.name,
        "amount": float(app.amount),
        "term_months": app.term_months,
        "status": app.status,
        "status_label": BANK_LABELS[AppStatus(app.status)],
        "stage_detail": app.stage_detail,
        "grade": scoring.grade if scoring else None,
        "score": scoring.score if scoring else None,
        "assignee": app.assignee.full_name if app.assignee else None,
        "submitted_at": app.submitted_at,
        "status_changed_at": app.status_changed_at,
        "review_due_at": app.review_due_at,
        "sla_overdue": bool(app.status == AppStatus.MANUAL_REVIEW and app.review_due_at and app.review_due_at < now),
        "triggers": ["Выдача не прошла — повторите её, когда АБС снова доступна"]
        if disbursement_failed(db, app)
        else _triggers(d),
        "awaiting_reply": waiting,
    }


def bank_application_out(db: Session, app: Application) -> dict:
    company: Company = app.company
    members = db.scalars(select(CompanyMember).where(CompanyMember.company_id == company.id))
    sources = {
        r.source: {"payload": r.payload, "fetched_at": r.fetched_at}
        for r in db.scalars(select(EnrichmentResult).where(EnrichmentResult.application_id == app.id))
    }
    scoring = latest_scoring(db, app.id)
    last_run = db.scalar(
        select(CheckResult.run_id).where(CheckResult.application_id == app.id).order_by(CheckResult.id.desc()).limit(1)
    )
    checks = (
        [
            {"code": c.code, "severity": c.severity, "message": c.message}
            for c in db.scalars(select(CheckResult).where(CheckResult.run_id == last_run).order_by(CheckResult.id))
        ]
        if last_run
        else []
    )
    decisions = [
        {
            "id": d.id,
            "created_at": d.created_at,
            "kind": d.kind,
            "outcome": d.outcome,
            "outcome_label": DECISION_LABELS[DecisionOutcome(d.outcome)],
            "reasons": d.reasons,
            "details": d.details,
            "decided_by": d.decided_by.full_name if d.decided_by else None,
            "comment_internal": d.comment_internal,
            "message_to_client": d.message_to_client,
            "policy_version": d.policy_version,
        }
        for d in db.scalars(
            select(Decision).where(Decision.application_id == app.id).order_by(Decision.created_at.desc())
        )
    ]
    offers = [
        offer_out(o)
        for o in db.scalars(select(Offer).where(Offer.application_id == app.id).order_by(Offer.created_at.desc()))
    ]
    info_requests = [
        info_request_out(r, db)
        for r in db.scalars(
            select(InfoRequest).where(InfoRequest.application_id == app.id).order_by(InfoRequest.created_at.desc())
        )
    ]
    events = [
        {**event_out(e), "client_visible": e.client_visible}
        for e in db.scalars(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == app.id)
            .order_by(ApplicationEvent.created_at, ApplicationEvent.id)
        )
    ]
    calls = [
        {
            "created_at": c.created_at,
            "service": c.service,
            "operation": c.operation,
            "status": c.status,
            "http_status": c.http_status,
            "duration_ms": c.duration_ms,
            "attempt": c.attempt,
            "error": c.error,
        }
        for c in db.scalars(
            select(IntegrationCall)
            .where(IntegrationCall.application_id == app.id)
            .order_by(IntegrationCall.id.desc())
            .limit(40)
        )
    ]
    loan = db.scalar(select(Loan).where(Loan.application_id == app.id))
    consents = [
        {"type": c.consent_type, "signed_at": c.signed_at, "signed_by": db.get(User, c.signed_by_id).full_name}
        for c in db.scalars(select(Consent).where(Consent.application_id == app.id))
    ]
    return {
        **bank_row(db, app),
        "purpose": app.purpose,
        "purpose_details": app.purpose_details,
        "declared_revenue": float(app.declared_revenue) if app.declared_revenue is not None else None,
        "has_collateral": app.has_collateral,
        "collateral_description": app.collateral_description,
        "owner_guarantee": app.owner_guarantee,
        "contact_name": app.contact_name,
        "contact_phone": app.contact_phone,
        "info_rounds": app.info_rounds,
        "created_by": app.created_by.full_name,
        "product": product_out(app.product),
        "company": {
            "id": company.id,
            "inn": company.inn,
            "ogrn": company.ogrn,
            "kind": company.kind,
            "short_name": company.short_name,
            "full_name": company.full_name,
            "okved_code": company.okved_code,
            "okved_name": company.okved_name,
            "registration_date": company.registration_date,
            "legal_address": company.legal_address,
            "client_since": company.client_since,
            "segment": company.segment,
            "settlement_account": company.settlement_account,
            "headcount": company.headcount,
            "members": [
                {
                    "full_name": m.user.full_name,
                    "position_label": POSITION_LABELS.get(m.position, m.position),
                    "can_sign": m.can_sign,
                }
                for m in members
            ],
        },
        "sources": sources,
        "scoring": (
            {
                "score": scoring.score,
                "grade": scoring.grade,
                "grade_label": GRADE_LABELS[scoring.grade],
                "pd": float(scoring.pd),
                "model_version": scoring.model_version,
                "factors": scoring.factors,
                "metrics": scoring.metrics,
                "created_at": scoring.created_at,
            }
            if scoring
            else None
        ),
        "checks": checks,
        "decisions": decisions,
        "offers": offers,
        "info_requests": info_requests,
        "events": events,
        "integration_calls": calls,
        "documents": [document_out(d) for d in app.documents],
        "consents": consents,
        "loan": loan_out(loan, app) if loan else None,
        # сколько места под новый платёж в ограничении 40% — с учётом всех кредитов компании на сейчас
        # (без отчёта БКИ кредиты в других банках неизвестны, а одобрить такую заявку всё равно нельзя)
        "load": assessment.current_load(db, app).to_dict()
        if app.status == AppStatus.MANUAL_REVIEW and "BKI" in sources
        else None,
        "permissions": {
            "can_decide": app.status == AppStatus.MANUAL_REVIEW,
            "can_rerun": app.status == AppStatus.MANUAL_REVIEW,
            "can_retry_disbursement": disbursement_failed(db, app),
        },
    }


def message_out(m: Message) -> dict:
    return {
        "id": m.id,
        "created_at": m.created_at,
        "author_role": m.author_role,
        "author_name": m.author.full_name if m.author else "Банк",
        "text": m.text,
    }


def dashboard(db: Session) -> dict:
    now = utcnow()
    sent = Application.submitted_at.is_not(None)  # черновики — не в работе банка
    by_status = dict(
        db.execute(select(Application.status, func.count()).where(sent).group_by(Application.status)).all()
    )
    total = sum(by_status.values())
    submitted = db.scalar(select(func.count()).where(Application.submitted_at.is_not(None))) or 0
    reached_offer = (
        db.scalar(
            select(func.count(func.distinct(ApplicationEvent.application_id))).where(
                ApplicationEvent.to_status == AppStatus.OFFER_READY
            )
        )
        or 0
    )
    issued = by_status.get(AppStatus.ISSUED, 0)
    declined = by_status.get(AppStatus.DECLINED, 0)

    final_decisions = list(
        db.execute(
            select(Decision.kind, func.count())
            .where(
                Decision.outcome.in_([DecisionOutcome.APPROVE, DecisionOutcome.COUNTER_OFFER, DecisionOutcome.DECLINE])
            )
            .group_by(Decision.kind)
        ).all()
    )
    kinds = dict(final_decisions)
    final_total = sum(kinds.values())
    auto_share = round(kinds.get("AUTO", 0) / final_total, 3) if final_total else None

    def avg_minutes(col_end, col_start) -> float | None:
        v = db.scalar(
            select(func.avg(func.extract("epoch", col_end - col_start))).where(
                col_end.is_not(None), col_start.is_not(None)
            )
        )
        return round(float(v) / 60, 1) if v is not None else None

    manual_queue = by_status.get(AppStatus.MANUAL_REVIEW, 0)
    overdue = (
        db.scalar(
            select(func.count()).where(Application.status == AppStatus.MANUAL_REVIEW, Application.review_due_at < now)
        )
        or 0
    )
    issued_amount = db.scalar(select(func.coalesce(func.sum(Loan.amount), 0))) or 0
    # выдачи, которые не прошли после всех попыток и ждут повтора специалистом
    failed_disbursements = sum(
        1
        for a in db.scalars(select(Application).where(Application.status == AppStatus.DISBURSEMENT))
        if disbursement_failed(db, a)
    )
    by_product = [
        {"product_code": code, "count": cnt, "amount": float(amt or 0)}
        for code, cnt, amt in db.execute(
            select(Application.product_code, func.count(), func.sum(Application.amount))
            .where(Application.submitted_at.is_not(None))
            .group_by(Application.product_code)
        ).all()
    ]
    hour_ago = now - timedelta(hours=1)
    integrations = [
        {
            "service": svc,
            "calls": cnt,
            "errors": int(errs or 0),
            "avg_ms": round(float(avg_ms or 0)),
        }
        for svc, cnt, errs, avg_ms in db.execute(
            select(
                IntegrationCall.service,
                func.count(),
                func.sum(case((IntegrationCall.status != "SUCCESS", 1), else_=0)),
                func.avg(IntegrationCall.duration_ms),
            )
            .where(IntegrationCall.created_at >= hour_ago)
            .group_by(IntegrationCall.service)
        ).all()
    ]
    recent = [
        {**event_out(e), "application_number": num, "application_id": e.application_id}
        for e, num in db.execute(
            select(ApplicationEvent, Application.number)
            .join(Application, Application.id == ApplicationEvent.application_id)
            .where(ApplicationEvent.event_type == "STATUS_CHANGED", sent)
            .order_by(ApplicationEvent.created_at.desc())
            .limit(12)
        ).all()
    ]
    return {
        "generated_at": now,
        "total": total,
        "by_status": [
            {"status": s, "label": BANK_LABELS[AppStatus(s)], "count": c} for s, c in sorted(by_status.items())
        ],
        "funnel": [
            {"stage": "Подано заявок", "count": submitted},
            {"stage": "Получили предложение", "count": reached_offer},
            {"stage": "Выдано кредитов", "count": issued},
        ],
        "declined": declined,
        "auto_decision_share": auto_share,
        "avg_minutes_to_decision": avg_minutes(Application.decided_at, Application.submitted_at),
        "avg_minutes_to_issue": avg_minutes(Application.issued_at, Application.submitted_at),
        "manual_queue": manual_queue,
        "failed_disbursements": failed_disbursements,
        "manual_overdue": overdue,
        "issued_amount": float(issued_amount),
        "by_product": by_product,
        "integrations_last_hour": integrations,
        "recent_events": recent,
    }


def revenue_estimate(stats_avg: float) -> float:
    return round(stats_avg * 12 / 10_000) * 10_000


def as_of_today() -> date:
    return local_today()
