"""Этапы кредитного конвейера, которые выполняет воркер.

Каждый этап идемпотентен: если заявка уже не в ожидаемом статусе (например, клиент её отозвал),
этап завершается без изменений. Состояние сохраняется в БД после каждого шага.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.config import get_settings
from app.domain import checks as checks_mod
from app.domain.enums import (
    DECISION_LABELS,
    AppStatus,
    ConsentType,
    DecisionKind,
    DecisionOutcome,
    InfoRequestStatus,
    JobStage,
    JobStatus,
    OfferStatus,
)
from app.domain.finance import add_months, build_annuity_schedule, build_revolving_schedule, money
from app.domain.policy import POLICY_VERSION, OfferDraft, PolicyResult, evaluate
from app.domain.scoring import MODEL_VERSION, score
from app.domain.text import fmt_money, lower_first
from app.integrations.client import SERVICE_FROM, SERVICE_TITLES, ExternalSystems, IntegrationError
from app.models import (
    Application,
    CheckResult,
    Consent,
    Decision,
    EnrichmentResult,
    InfoRequest,
    Loan,
    Offer,
    PipelineJob,
    ScoringResult,
)
from app.services import assessment
from app.services.views import schedule_start
from app.services.workflow import add_event, enqueue, key_rate, local_today, transition, utcnow

log = logging.getLogger("pipeline")
settings = get_settings()

ENRICHMENT_CACHE_HOURS = 24


# --------------------------------------------------------------------------- PRECHECK


def stage_precheck(db: Session, app: Application, job: PipelineJob) -> None:
    if app.status != AppStatus.PRECHECK:
        return
    consents = list(db.scalars(select(Consent).where(Consent.application_id == app.id)))
    results = checks_mod.precheck(app, app.product, app.company, app.documents, consents)
    run_id = str(uuid.uuid4())
    for c in results:
        db.add(
            CheckResult(
                application_id=app.id,
                stage="PRECHECK",
                code=c.code,
                severity=c.severity,
                message=c.message,
                run_id=run_id,
            )
        )
    missing = [c for c in results if c.severity in ("MISSING", "STOP")]
    if missing:
        items = [c.info_item or {"code": c.code, "type": "COMMENT", "label": c.message} for c in missing]
        db.add(
            InfoRequest(
                application_id=app.id,
                items=items,
                message="Чтобы продолжить рассмотрение, дополните заявку.",
                status=InfoRequestStatus.OPEN,
            )
        )
        app.info_rounds += 1
        transition(
            db,
            app,
            AppStatus.NEEDS_INFO,
            title="Нужно дополнить заявку",
            payload={"missing": [c.code for c in missing]},
        )
        return
    transition(
        db,
        app,
        AppStatus.ENRICHMENT,
        title="Данные проверены, собираем сведения для оценки",
        stage_detail="Запрашиваем выписку из ЕГРИП" if app.company.kind == "IP" else "Запрашиваем выписку из ЕГРЮЛ",
    )
    enqueue(db, app, JobStage.ENRICHMENT)


# --------------------------------------------------------------------------- ENRICHMENT


def _upsert_enrichment(db: Session, app: Application, source: str, payload: dict) -> None:
    row = db.scalar(
        select(EnrichmentResult).where(EnrichmentResult.application_id == app.id, EnrichmentResult.source == source)
    )
    if row is None:
        db.add(EnrichmentResult(application_id=app.id, source=source, payload=payload, fetched_at=utcnow()))
    else:
        row.payload = payload
        row.fetched_at = utcnow()


def stage_enrichment(db: Session, app: Application, job: PipelineJob) -> None:
    if app.status != AppStatus.ENRICHMENT:
        return
    cached = {
        r.source: r
        for r in db.scalars(select(EnrichmentResult).where(EnrichmentResult.application_id == app.id))
        if r.fetched_at > utcnow() - timedelta(hours=ENRICHMENT_CACHE_HOURS)
    }
    consent = db.scalar(
        select(Consent)
        .where(Consent.application_id == app.id, Consent.consent_type == ConsentType.BKI_REQUEST)
        .order_by(Consent.signed_at.desc())
    )
    inn = app.company.inn
    registry = "ЕГРИП" if app.company.kind == "IP" else "ЕГРЮЛ"
    with ExternalSystems(application_id=app.id, attempt=job.attempts) as ext:
        steps = [
            ("EGRUL", f"Запрашиваем выписку из {registry}", lambda: ext.egrul(inn)),
            ("FNS", "Проверяем компанию в сервисе ФНС «Прозрачный бизнес»", lambda: ext.fns(inn)),
            ("BKI", "Запрашиваем кредитную историю в БКИ", lambda: ext.bki(inn, str(consent.id) if consent else "")),
        ]
        for source, detail, call in steps:
            if source in cached:
                continue
            app.stage_detail = detail
            db.commit()  # клиент видит, на каком шаге заявка
            payload = call()
            _upsert_enrichment(db, app, source, payload)
            add_event(
                db,
                app,
                "SOURCE_RECEIVED",
                f"Получены данные: {SERVICE_TITLES[source]}",
                payload={"source": source},
                client_visible=False,
            )
            db.commit()

    stats = assessment.company_turnover(db, app.company_id)
    _upsert_enrichment(
        db,
        app,
        "INTERNAL",
        {
            "months": stats.months,
            "avg_monthly_turnover": round(stats.avg_monthly, 2),
            "turnover_cv": round(stats.cv, 4),
            "turnover_trend": round(stats.trend, 4),
            "series": stats.series,
            "client_since": app.company.client_since.isoformat(),
            "settlement_account": app.company.settlement_account,
        },
    )
    transition(
        db,
        app,
        AppStatus.SCORING,
        title="Сведения получены, оцениваем заявку",
        stage_detail="Рассчитываем кредитный рейтинг",
    )
    enqueue(db, app, JobStage.SCORING)


def on_enrichment_failed(db: Session, app: Application, error: Exception) -> None:
    if app.status != AppStatus.ENRICHMENT:
        return
    if not isinstance(error, IntegrationError):
        # источники ответили, а упал расчёт (например, оборотов по счёту) — это технический сбой этапа
        on_stage_failed(db, app, error, JobStage.ENRICHMENT)
        return
    service = getattr(error, "service", "")
    source = SERVICE_FROM.get(service, "из внешнего источника")
    reason = getattr(error, "reason", str(error))
    decision = Decision(
        application_id=app.id,
        kind=DecisionKind.AUTO,
        outcome=DecisionOutcome.MANUAL_REVIEW,
        reasons=[
            {
                "code": "integration_unavailable",
                "kind": "MANUAL",
                "text": f"Не удалось получить данные {source}: {reason}",
                "client_text": "Один из внешних источников данных временно недоступен — заявку проверит специалист",
            }
        ],
        details={"error": str(error), "service": service},
        policy_version=POLICY_VERSION,
    )
    db.add(decision)
    app.review_due_at = utcnow() + timedelta(hours=settings.manual_review_sla_hours)
    transition(
        db,
        app,
        AppStatus.MANUAL_REVIEW,
        title="Передали заявку специалисту: внешний источник данных недоступен",
        payload={"service": service},
    )


# --------------------------------------------------------------------------- SCORING


def stage_scoring(db: Session, app: Application, job: PipelineJob) -> None:
    if app.status != AppStatus.SCORING:
        return
    sources = assessment.enrichment(db, app.id)
    stats = assessment.company_turnover(db, app.company_id)
    inp = assessment.build_scoring_input(
        app, stats, sources, key_rate(db), local_today(), assessment.own_obligations(db, app.company_id, app.id)
    )
    outcome = score(inp)
    db.add(
        ScoringResult(
            application_id=app.id,
            model_version=MODEL_VERSION,
            score=outcome.score,
            grade=outcome.grade,
            pd=outcome.pd,
            factors=[f.to_dict() for f in outcome.factors],
            metrics=outcome.metrics,
        )
    )
    add_event(
        db,
        app,
        "SCORED",
        f"Скоринг: {outcome.score} баллов, рейтинг {outcome.grade}",
        payload={"score": outcome.score, "grade": outcome.grade},
        client_visible=False,
    )
    transition(
        db,
        app,
        AppStatus.DECISION,
        title="Рейтинг рассчитан, подбираем условия",
        stage_detail="Подбираем сумму, срок и ставку",
    )
    enqueue(db, app, JobStage.DECISION)


# --------------------------------------------------------------------------- DECISION


def create_offers(db: Session, app: Application, decision: Decision, drafts: list[OfferDraft]) -> list[Offer]:
    for old in db.scalars(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACTIVE)):
        old.status = OfferStatus.SUPERSEDED
    valid_until = utcnow() + timedelta(days=settings.offer_valid_days)
    offers = []
    for d in drafts:
        o = Offer(
            application_id=app.id,
            decision_id=decision.id,
            product_code=d.product_code,
            amount=money(d.amount),
            term_months=d.term_months,
            rate=d.rate,
            monthly_payment=money(d.monthly_payment),
            total_interest=money(d.total_interest),
            conditions=d.conditions,
            pricing=d.pricing,
            is_counter=d.is_counter,
            label=d.label,
            status=OfferStatus.ACTIVE,
            valid_until=valid_until,
        )
        db.add(o)
        offers.append(o)
    return offers


def apply_policy_result(db: Session, app: Application, result: PolicyResult) -> Decision:
    decision = Decision(
        id=uuid.uuid4(),
        application_id=app.id,
        kind=DecisionKind.AUTO,
        outcome=result.outcome,
        reasons=[r.to_dict() for r in result.reasons],
        details={**result.details, "recommendation": result.recommendation},
        policy_version=POLICY_VERSION,
    )
    db.add(decision)
    db.flush()
    label = DECISION_LABELS[DecisionOutcome(result.outcome)]

    if result.outcome in (DecisionOutcome.APPROVE, DecisionOutcome.COUNTER_OFFER):
        create_offers(db, app, decision, result.offers)
        app.decided_at = utcnow()
        title = (
            "Заявка одобрена — готово предложение"
            if result.outcome == DecisionOutcome.APPROVE
            else "Готово встречное предложение"
        )
        transition(db, app, AppStatus.OFFER_READY, title=title, payload={"decision": label})
    elif result.outcome == DecisionOutcome.NEEDS_INFO:
        db.add(
            InfoRequest(
                application_id=app.id,
                items=result.info_items,
                message="Чтобы рассчитать сумму с учётом всей выручки бизнеса, нам нужны уточнения.",
                status=InfoRequestStatus.OPEN,
            )
        )
        app.info_rounds += 1
        transition(db, app, AppStatus.NEEDS_INFO, title="Нужно подтвердить выручку")
    elif result.outcome == DecisionOutcome.MANUAL_REVIEW:
        app.review_due_at = utcnow() + timedelta(hours=settings.manual_review_sla_hours)
        transition(
            db,
            app,
            AppStatus.MANUAL_REVIEW,
            title="Заявку рассматривает кредитный специалист",
            payload={"triggers": result.details.get("manual_triggers", [])},
        )
    else:
        app.decided_at = utcnow()
        reason = next((r.client_text for r in result.reasons if r.kind == "STOP"), "")
        transition(db, app, AppStatus.DECLINED, title=f"Отказ: {lower_first(reason)}" if reason else "Отказ")
    return decision


def stage_decision(db: Session, app: Application, job: PipelineJob) -> None:
    if app.status != AppStatus.DECISION:
        return
    sources = assessment.enrichment(db, app.id)
    stats = assessment.company_turnover(db, app.company_id)
    row = db.scalar(
        select(ScoringResult).where(ScoringResult.application_id == app.id).order_by(ScoringResult.id.desc())
    )
    scoring = assessment.scoring_from_row(row)
    inp = assessment.build_policy_input(db, app, scoring, sources, stats, key_rate(db), local_today())
    result = evaluate(inp)
    apply_policy_result(db, app, result)


# --------------------------------------------------------------------------- DISBURSEMENT


def stage_disbursement(db: Session, app: Application, job: PipelineJob) -> None:
    if app.status != AppStatus.DISBURSEMENT:
        return
    offer = db.scalar(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACCEPTED))
    contract_number = f"КД-{app.number}"
    app.stage_detail = "Открываем ссудный счёт в АБС"
    db.commit()
    with ExternalSystems(application_id=app.id, attempt=job.attempts) as ext:
        res = ext.abs_disburse(
            {
                "inn": app.company.inn,
                "contract_number": contract_number,
                "product_code": offer.product_code,
                "amount": float(offer.amount),
                "term_months": offer.term_months,
                "rate": float(offer.rate),
                "settlement_account": app.company.settlement_account,
                "idempotency_key": str(app.id),
            }
        )
    issued_at = utcnow()
    start = schedule_start(offer)  # дата подписания: график кредита совпадает с подписанным
    if offer.product.repayment_type == "REVOLVING":
        schedule = build_revolving_schedule(float(offer.amount), float(offer.rate), offer.term_months, start)
    else:
        schedule = build_annuity_schedule(float(offer.amount), float(offer.rate), offer.term_months, start)
    db.add(
        Loan(
            application_id=app.id,
            company_id=app.company_id,
            product_code=offer.product_code,
            contract_number=contract_number,
            loan_account=res["loan_account"],
            amount=offer.amount,
            term_months=offer.term_months,
            rate=offer.rate,
            monthly_payment=offer.monthly_payment,
            issued_at=issued_at,
            first_payment_date=add_months(start, 1),
            status="ACTIVE",
            schedule=schedule,
        )
    )
    app.issued_at = issued_at
    amount = fmt_money(offer.amount)
    title = (
        f"Лимит овердрафта {amount} установлен на счёт {app.company.settlement_account}"
        if offer.product.repayment_type == "REVOLVING"
        else f"{amount} зачислены на счёт {app.company.settlement_account}"
    )
    transition(
        db,
        app,
        AppStatus.ISSUED,
        title=title,
        payload={"loan_account": res["loan_account"], "operation_id": res.get("operation_id")},
    )


HANDLERS = {
    JobStage.PRECHECK: stage_precheck,
    JobStage.ENRICHMENT: stage_enrichment,
    JobStage.SCORING: stage_scoring,
    JobStage.DECISION: stage_decision,
    JobStage.DISBURSEMENT: stage_disbursement,
}


def on_disbursement_failed(db: Session, app: Application, error: Exception) -> None:
    """Выдача не прошла после всех попыток (например, АБС долго недоступна). Заявка остаётся на выдаче:
    договор подписан, отменить его нельзя. Специалист видит её в очереди и повторяет выдачу,
    когда АБС снова доступна; запрос в АБС идемпотентен, второго кредита не будет."""
    if app.status != AppStatus.DISBURSEMENT:
        return
    service = getattr(error, "service", "")
    title = SERVICE_TITLES.get(service, "учётная система банка")
    app.stage_detail = "Выдача задерживается — специалист банка повторит её"
    add_event(
        db,
        app,
        "DISBURSEMENT_FAILED",
        f"Выдача не прошла: {lower_first(title)} не ответила после всех попыток. Нужен повтор выдачи",
        payload={"error": str(error)[:300], "service": service},
        client_visible=False,
    )


STAGE_TITLES = {
    JobStage.PRECHECK: "Проверка полноты",
    JobStage.ENRICHMENT: "Сбор сведений",
    JobStage.SCORING: "Скоринг",
    JobStage.DECISION: "Расчёт решения",
}


def on_stage_failed(db: Session, app: Application, error: Exception, stage: str) -> None:
    """Проверка полноты, скоринг или расчёт решения не выполнились после всех попыток (например, из-за
    ошибки в данных). Заявка не остаётся на этапе: она уходит в очередь специалиста с причиной «технический
    сбой». Специалист повторяет проверку, когда ошибку устранят, или принимает решение сам."""
    if app.status != stage:
        return
    decision = Decision(
        application_id=app.id,
        kind=DecisionKind.AUTO,
        outcome=DecisionOutcome.MANUAL_REVIEW,
        reasons=[
            {
                "code": "technical_failure",
                "kind": "MANUAL",
                "text": f"Этап «{STAGE_TITLES[stage]}» не выполнен после всех попыток: {str(error)[:300]}. "
                "Повторите проверку, когда ошибку устранят, или примите решение вручную",
                "client_text": "Автоматическая проверка не завершилась — заявку рассмотрит специалист",
            }
        ],
        details={"error": str(error)[:500], "stage": stage},
        policy_version=POLICY_VERSION,
    )
    db.add(decision)
    app.review_due_at = utcnow() + timedelta(hours=settings.manual_review_sla_hours)
    transition(
        db,
        app,
        AppStatus.MANUAL_REVIEW,
        title="Передали заявку специалисту: автоматическая проверка не завершилась",
        payload={"stage": stage},
    )


def _stage_failed(stage: str):
    return lambda db, app, error: on_stage_failed(db, app, error, stage)


# у каждого этапа есть обработчик: после исчерпания попыток заявка не может «зависнуть» на этапе
FAILURE_HANDLERS = {
    JobStage.PRECHECK: _stage_failed(JobStage.PRECHECK),
    JobStage.ENRICHMENT: on_enrichment_failed,
    JobStage.SCORING: _stage_failed(JobStage.SCORING),
    JobStage.DECISION: _stage_failed(JobStage.DECISION),
    JobStage.DISBURSEMENT: on_disbursement_failed,
}


AUTOMATIC_STAGES = (JobStage.PRECHECK, JobStage.ENRICHMENT, JobStage.SCORING, JobStage.DECISION)


def stuck_applications(db: Session) -> list[tuple[Application, PipelineJob]]:
    """Заявки на автоматическом этапе, последняя задача которого упала и больше не повторяется. Так бывает,
    если сбой случился до того, как у этапа появился обработчик, или сам обработчик не сработал."""
    last_job = (
        select(PipelineJob.application_id, func.max(PipelineJob.id).label("job_id"))
        .group_by(PipelineJob.application_id)
        .subquery()
    )
    rows = db.execute(
        select(Application, PipelineJob)
        .join(last_job, last_job.c.application_id == Application.id)
        .join(PipelineJob, PipelineJob.id == last_job.c.job_id)
        .where(
            Application.status.in_(AUTOMATIC_STAGES),
            PipelineJob.status == JobStatus.FAILED,
            PipelineJob.stage == Application.status,
        )
    ).all()
    return [(app, job) for app, job in rows]


def recover_stuck_applications(db: Session) -> int:
    """Передаёт специалисту заявки, которые иначе так и остались бы на этапе (см. stuck_applications)."""
    recovered = 0
    for app, job in stuck_applications(db):
        try:
            FAILURE_HANDLERS[JobStage(job.stage)](db, app, RuntimeError(job.last_error or "этап не выполнен"))
            db.commit()
            recovered += 1
        except StaleDataError:
            db.rollback()  # заявку в это же время изменили (или её взял другой воркер) — посмотрим в следующий раз
        except Exception:  # noqa: BLE001 — одна заявка не должна мешать остальным
            db.rollback()
            log.exception("не удалось передать специалисту заявку после сбоя этапа", extra={"application": app.number})
    return recovered


def expire_offers(db: Session) -> int:
    """Закрывает заявки, по которым клиент не принял предложение или не подписал договор вовремя."""
    now = utcnow()
    expired = 0
    apps = db.scalars(
        select(Application).where(Application.status.in_([AppStatus.OFFER_READY, AppStatus.SIGNING]))
    ).all()
    for app in apps:
        if app.status == AppStatus.OFFER_READY:
            offers = list(
                db.scalars(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACTIVE))
            )
            title = "Срок действия предложения истёк"
        else:
            # выбранный вариант тоже действует до даты в предложении: договор по нему после срока не подписать
            offers = list(
                db.scalars(select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACCEPTED))
            )
            title = "Срок действия предложения истёк до подписания договора"
        if offers and all(o.valid_until < now for o in offers):
            for o in offers:
                o.status = OfferStatus.EXPIRED
            transition(db, app, AppStatus.EXPIRED, title=title)
            expired += 1
    return expired
