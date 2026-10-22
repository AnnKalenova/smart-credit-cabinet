"""Сборка ответов API из ORM-объектов: отдельно для клиента и для банка."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.domain.enums import (
    DECISION_LABELS,
    DOC_TYPES,
    POSITION_LABELS,
    AppStatus,
    DecisionOutcome,
    InfoRequestStatus,
    OfferStatus,
)
from app.domain.finance import build_annuity_schedule, build_revolving_schedule
from app.domain.products import required_documents
from app.domain.scoring import GRADE_LABELS
from app.domain.statuses import BANK_LABELS, CLIENT_CANCELLABLE, client_view
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
    ScoringResult,
    User,
)
from app.services.workflow import utcnow


def fmt_money(v) -> str:
    return f"{float(v):,.0f}".replace(",", " ") + " ₽"


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


def schedule_for(offer) -> list[dict]:
    start = (offer.accepted_at or utcnow()).date()
    if offer.product.repayment_type == "REVOLVING":
        return build_revolving_schedule(float(offer.amount), float(offer.rate), offer.term_months, start)
    return build_annuity_schedule(float(offer.amount), float(offer.rate), offer.term_months, start)


def offer_out(o: Offer, full_schedule: bool = False) -> dict:
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
    }


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


def summary_out(app: Application) -> dict:
    view = client_view(app.status)
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
    return db.scalar(select(ScoringResult).where(ScoringResult.application_id == app_id).order_by(ScoringResult.id.desc()))


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
            select(Offer).where(Offer.application_id == app.id, Offer.status.in_(wanted)).order_by(Offer.is_counter, Offer.amount.desc())
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
    reasons = [r["client_text"] for r in d.reasons if r.get("client_text") and r.get("kind") in ("STOP", "LIMIT", "INFO")]
    scoring = latest_scoring(db, app.id)
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


def client_status_view(app: Application, offers: list[Offer]) -> dict:
    view = client_view(app.status)
    if app.status == AppStatus.MANUAL_REVIEW and app.review_due_at:
        view["next"] = (
            "Решение появится здесь же, в кабинете, ориентировочно до "
            f"{app.review_due_at.astimezone().strftime('%d.%m %H:%M')}. Если понадобятся документы, мы напишем в этой заявке."
        )
    if app.status == AppStatus.OFFER_READY and offers:
        until = min(o.valid_until for o in offers)
        view["next"] = f"Выберите вариант и примите предложение до {until.astimezone().strftime('%d.%m.%Y')}."
    return view


def application_out(db: Session, app: Application, member: CompanyMember) -> dict:
    product = app.product
    offers = visible_offers(db, app)
    have = {d.doc_type for d in app.documents}
    req = [
        {"doc_type": t, "label": DOC_TYPES[t], "attached": t in have}
        for t in required_documents(product, float(app.amount), app.purpose, app.company.kind)
    ]
    consents = [
        {"type": c.consent_type, "text": c.text, "signed_at": c.signed_at, "signed_by": db.get(User, c.signed_by_id).full_name}
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
        for m in db.scalars(select(CompanyMember).where(CompanyMember.company_id == app.company_id, CompanyMember.can_sign.is_(True)))
    ]
    data = summary_out(app)
    data["status_view"] = client_status_view(app, offers)
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
            "info_request": info_request_out(info, db) if info and (info.status == InfoRequestStatus.OPEN or app.status != AppStatus.DRAFT) else None,
            "offers": [offer_out(o) for o in offers],
            "decision": client_decision_view(db, app),
            "events": [event_out(e) for e in events],
            "loan": loan_out(loan, app, with_schedule=False) if loan else None,
            "review_due_at": app.review_due_at,
            "parent_id": app.parent_id,
            "permissions": {
                "can_edit": app.status == AppStatus.DRAFT,
                "can_submit": app.status == AppStatus.DRAFT and member.can_sign,
                "can_sign": member.can_sign,
                "can_cancel": app.status in CLIENT_CANCELLABLE,
                "can_answer": app.status == AppStatus.NEEDS_INFO,
                "can_accept": app.status == AppStatus.OFFER_READY,
                "can_reapply": app.status in (AppStatus.DECLINED, AppStatus.EXPIRED, AppStatus.CLIENT_DECLINED, AppStatus.CANCELLED),
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


def bank_row(db: Session, app: Application) -> dict:
    scoring = latest_scoring(db, app.id)
    d = latest_decision(db, app.id)
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
        "triggers": _triggers(d),
    }


def bank_application_out(db: Session, app: Application) -> dict:
    company: Company = app.company
    members = db.scalars(select(CompanyMember).where(CompanyMember.company_id == company.id))
    sources = {r.source: {"payload": r.payload, "fetched_at": r.fetched_at} for r in db.scalars(select(EnrichmentResult).where(EnrichmentResult.application_id == app.id))}
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
        for d in db.scalars(select(Decision).where(Decision.application_id == app.id).order_by(Decision.created_at.desc()))
    ]
    offers = [
        offer_out(o)
        for o in db.scalars(select(Offer).where(Offer.application_id == app.id).order_by(Offer.created_at.desc()))
    ]
    info_requests = [
        info_request_out(r, db)
        for r in db.scalars(select(InfoRequest).where(InfoRequest.application_id == app.id).order_by(InfoRequest.created_at.desc()))
    ]
    events = [
        {**event_out(e), "client_visible": e.client_visible}
        for e in db.scalars(
            select(ApplicationEvent).where(ApplicationEvent.application_id == app.id).order_by(ApplicationEvent.created_at, ApplicationEvent.id)
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
            select(IntegrationCall).where(IntegrationCall.application_id == app.id).order_by(IntegrationCall.id.desc()).limit(40)
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
                {"full_name": m.user.full_name, "position_label": POSITION_LABELS.get(m.position, m.position), "can_sign": m.can_sign}
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
        "permissions": {
            "can_decide": app.status == AppStatus.MANUAL_REVIEW,
            "can_rerun": app.status == AppStatus.MANUAL_REVIEW,
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
    by_status = dict(db.execute(select(Application.status, func.count()).group_by(Application.status)).all())
    total = sum(by_status.values())
    submitted = db.scalar(select(func.count()).where(Application.submitted_at.is_not(None))) or 0
    reached_offer = db.scalar(
        select(func.count(func.distinct(ApplicationEvent.application_id))).where(ApplicationEvent.to_status == AppStatus.OFFER_READY)
    ) or 0
    issued = by_status.get(AppStatus.ISSUED, 0)
    declined = by_status.get(AppStatus.DECLINED, 0)

    final_decisions = list(
        db.execute(
            select(Decision.kind, func.count())
            .where(Decision.outcome.in_([DecisionOutcome.APPROVE, DecisionOutcome.COUNTER_OFFER, DecisionOutcome.DECLINE]))
            .group_by(Decision.kind)
        ).all()
    )
    kinds = dict(final_decisions)
    final_total = sum(kinds.values())
    auto_share = round(kinds.get("AUTO", 0) / final_total, 3) if final_total else None

    def avg_minutes(col_end, col_start) -> float | None:
        v = db.scalar(
            select(func.avg(func.extract("epoch", col_end - col_start))).where(col_end.is_not(None), col_start.is_not(None))
        )
        return round(float(v) / 60, 1) if v is not None else None

    manual_queue = by_status.get(AppStatus.MANUAL_REVIEW, 0)
    overdue = db.scalar(
        select(func.count()).where(Application.status == AppStatus.MANUAL_REVIEW, Application.review_due_at < now)
    ) or 0
    issued_amount = db.scalar(select(func.coalesce(func.sum(Loan.amount), 0))) or 0
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
        {**event_out(e), "application_number": num}
        for e, num in db.execute(
            select(ApplicationEvent, Application.number)
            .join(Application, Application.id == ApplicationEvent.application_id)
            .where(ApplicationEvent.event_type == "STATUS_CHANGED")
            .order_by(ApplicationEvent.created_at.desc())
            .limit(12)
        ).all()
    ]
    return {
        "generated_at": now,
        "total": total,
        "by_status": [{"status": s, "label": BANK_LABELS[AppStatus(s)], "count": c} for s, c in sorted(by_status.items())],
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
        "manual_overdue": overdue,
        "issued_amount": float(issued_amount),
        "by_product": by_product,
        "integrations_last_hour": integrations,
        "recent_events": recent,
    }


def revenue_estimate(stats_avg: float) -> float:
    return round(stats_avg * 12 / 10_000) * 10_000


def as_of_today() -> date:
    return utcnow().date()
