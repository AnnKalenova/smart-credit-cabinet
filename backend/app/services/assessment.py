"""Сборка входных данных для скоринга и политики из БД, подбор продукта, предодобренный лимит."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.enums import AppStatus, DecisionKind, DecisionOutcome, DocType, OfferStatus, Purpose
from app.domain.finance import floor_to, offer_economics
from app.domain.policy import (
    CONFIRMED_REVENUE_CAP,
    DTI_CAP,
    GRADE_LIMIT_FACTOR,
    MANUAL_DECLINE_MEMORY_DAYS,
    STATEMENT_HAIRCUT,
    PolicyInput,
    ProductParams,
    indicative_rate,
    max_limit,
    price,
)
from app.domain.products import pledged, required_documents
from app.domain.scoring import Factor, ScoringInput, ScoringOutcome, TurnoverStats, months_between, turnover_stats
from app.domain.text import fmt_money, fmt_rate, months_range
from app.models import (
    AccountTurnover,
    Application,
    Company,
    Decision,
    EnrichmentResult,
    Loan,
    Offer,
    Product,
    ScoringResult,
)


def company_turnover(db: Session, company_id) -> TurnoverStats:
    rows = db.execute(
        select(AccountTurnover.month, AccountTurnover.credit_turnover, AccountTurnover.debit_turnover)
        .where(AccountTurnover.company_id == company_id)
        .order_by(AccountTurnover.month)
    ).all()
    return turnover_stats([(m, float(c), float(d)) for m, c, d in rows])


def enrichment(db: Session, app_id) -> dict[str, dict]:
    rows = db.scalars(select(EnrichmentResult).where(EnrichmentResult.application_id == app_id))
    return {r.source: r.payload for r in rows}


def revenue_confirmed(app: Application) -> bool:
    return any(d.doc_type == DocType.BANK_STATEMENT for d in app.documents)


def effective_turnover(stats: TurnoverStats, app: Application) -> float:
    """Если клиент подтвердил выручку выпиской из другого банка — учитываем её с дисконтом 20%,
    но не больше чем вдвое выше поступлений на счёт в нашем банке."""
    base = stats.avg_monthly
    if revenue_confirmed(app) and app.declared_revenue:
        confirmed = float(app.declared_revenue) / 12 * STATEMENT_HAIRCUT
        base = max(base, min(confirmed, base * CONFIRMED_REVENUE_CAP))
    return base


def own_obligations(db: Session, company_id, exclude_app_id=None) -> float:
    """Платежи по кредитам, которые этот банк уже выдал компании или вот-вот выдаст (договор на подписании
    или в выдаче). В отчёте БКИ их ещё нет, поэтому в долговую нагрузку они добавляются здесь."""
    total = sum(
        float(x)
        for x in db.scalars(select(Loan.monthly_payment).where(Loan.company_id == company_id, Loan.status == "ACTIVE"))
    )
    q = (
        select(Offer.monthly_payment)
        .join(Application, Offer.application_id == Application.id)
        .where(
            Application.company_id == company_id,
            Application.status.in_([AppStatus.SIGNING, AppStatus.DISBURSEMENT]),
            Offer.status == OfferStatus.ACCEPTED,
        )
    )
    if exclude_app_id is not None:
        q = q.where(Application.id != exclude_app_id)
    total += sum(float(x) for x in db.scalars(q))
    return round(total, 2)


def existing_payments(db: Session, app: Application, sources: dict[str, dict] | None = None) -> float:
    """Все платежи компании по кредитам, кроме этой заявки: другие банки (по отчёту БКИ из этой заявки)
    и наш банк (выданные кредиты, договоры на подписании и в выдаче)."""
    bki = (enrichment(db, app.id) if sources is None else sources).get("BKI", {})
    return round(float(bki.get("total_monthly_payment", 0) or 0) + own_obligations(db, app.company_id, app.id), 2)


def _pct(share: float) -> str:
    return fmt_rate(round(share * 100, 1))  # 46,1% и 40%


@dataclass
class LoadRoom:
    """Сколько места под новый платёж осталось в ограничении долговой нагрузки (DTI_CAP)."""

    existing: float  # платежи по остальным кредитам компании
    income: float  # месячные поступления, от которых считается ограничение
    new_obligations: bool = True  # нагрузка выросла после решения банка (False — неизвестно, с какой он решал)

    @property
    def room(self) -> float:
        return DTI_CAP * self.income - self.existing

    def fits(self, payment: float) -> bool:
        return payment <= self.room + 0.5  # копейки округления платежа не в счёт

    def share(self, payment: float) -> float:
        return (self.existing + payment) / self.income if self.income > 0 else 1.0

    def _over(self, payment: float) -> str:
        return (
            f"вместе с ним платежи по кредитам компании составят {_pct(self.share(payment))} поступлений на счёт, "
            f"а допустимо не больше {_pct(DTI_CAP)}"
        )

    def _room_text(self) -> str:
        if self.room >= 1000:
            return f"Сейчас помещается платёж до {fmt_money(floor_to(self.room, 100))} в месяц"
        return "Свободного места под новый платёж сейчас нет"

    def refusal(self, payment: float) -> str:
        """Почему клиенту нельзя принять вариант."""
        lead = (
            "После решения банка у компании появились новые кредиты, и этот вариант больше не помещается"
            if self.new_obligations
            else "Этот вариант не помещается"
        )
        tail = (
            " — выберите вариант с меньшим платежом или подберите условия в конструкторе"
            if self.room >= 1000
            else ". Подберите условия в конструкторе: он учитывает все действующие кредиты"
        )
        return f"{lead} в допустимую долговую нагрузку: {self._over(payment)}. {self._room_text()}{tail}"

    def analyst_refusal(self, number: int, payment: float) -> str:
        """Почему специалист не может предложить вариант."""
        return (
            f"Вариант {number}: платёж {fmt_money(payment)} в месяц не помещается в ограничение долговой нагрузки — "
            f"{self._over(payment)}. {self._room_text()}"
        )

    def to_dict(self) -> dict:
        return {
            "existing_monthly_payments": round(self.existing, 2),
            "monthly_income": round(self.income, 2),
            "cap": DTI_CAP,
            "room": floor_to(self.room, 100),  # как в тексте отказа: «помещается платёж до …»
        }


def current_load(db: Session, app: Application) -> LoadRoom:
    """Нагрузка компании сейчас: все её платежи по кредитам, кроме этой заявки, и поступления на счёт."""
    return LoadRoom(existing_payments(db, app), effective_turnover(company_turnover(db, app.company_id), app))


def load_room(db: Session, app: Application, decision: Decision | None) -> LoadRoom | None:
    """Перепроверка нагрузки перед принятием предложения. Решение банка считало нагрузку на свой момент;
    если с тех пор у компании появились новые обязательства (выдан кредит или подписывается договор по другой
    заявке), возвращает остаток места под платёж. None — новых обязательств нет, решение учитывает всю нагрузку.

    Так несколько одновременно полученных предложений нельзя принять сверх ограничения 40%. Решение без
    сохранённой нагрузки (принятое до этой проверки) перепроверяется всегда."""
    existing = existing_payments(db, app)
    known = (decision.details or {}).get("existing_monthly_payments") if decision else None
    if known is not None and existing <= float(known) + 0.5:
        return None
    income = effective_turnover(company_turnover(db, app.company_id), app)
    return LoadRoom(existing, income, new_obligations=known is not None)


def prior_manual_decline(db: Session, company_id, exclude_app_id, as_of: date) -> str | None:
    """Отказ кредитного специалиста по другой заявке компании за последние 90 дней — «№ … от …»."""
    since = as_of - timedelta(days=MANUAL_DECLINE_MEMORY_DAYS)
    row = db.execute(
        select(Application.number, Decision.created_at)
        .join(Decision, Decision.application_id == Application.id)
        .where(
            Application.company_id == company_id,
            Application.id != exclude_app_id,
            Decision.kind == DecisionKind.MANUAL,
            Decision.outcome == DecisionOutcome.DECLINE,
            Decision.created_at >= since,
        )
        .order_by(Decision.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    number, decided_at = row
    local = decided_at.astimezone(ZoneInfo(get_settings().display_timezone))
    return f"№ {number} от {local.strftime('%d.%m.%Y')}"


def active_products(db: Session) -> list[Product]:
    return list(db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.sort_order)))


def build_scoring_input(
    app: Application,
    stats: TurnoverStats,
    sources: dict[str, dict],
    key_rate: float,
    as_of: date,
    other_payments: float = 0.0,
) -> ScoringInput:
    product = ProductParams.from_orm(app.product)
    rate = indicative_rate(product, key_rate)
    new_payment, _ = offer_economics(float(app.amount), rate, app.term_months, product.repayment_type)
    company = app.company
    return ScoringInput(
        as_of=as_of,
        company_kind=company.kind,
        registration_date=company.registration_date,
        client_since=company.client_since,
        okved_code=company.okved_code,
        turnover=stats,
        effective_monthly_turnover=effective_turnover(stats, app),
        new_monthly_payment=new_payment,
        egrul=sources.get("EGRUL", {}),
        fns=sources.get("FNS", {}),
        bki=sources.get("BKI", {}),
        has_collateral=pledged(app.product, app.has_collateral),
        owner_guarantee=app.owner_guarantee,
        other_monthly_payments=other_payments,
    )


def scoring_from_row(row: ScoringResult) -> ScoringOutcome:
    return ScoringOutcome(
        score=row.score,
        grade=row.grade,
        pd=float(row.pd),
        factors=[Factor(**f) for f in row.factors],
        metrics=row.metrics,
    )


def build_policy_input(
    db: Session,
    app: Application,
    scoring: ScoringOutcome,
    sources: dict[str, dict],
    stats: TurnoverStats,
    key_rate: float,
    as_of: date,
) -> PolicyInput:
    company = app.company
    return PolicyInput(
        product=ProductParams.from_orm(app.product),
        alt_products=[ProductParams.from_orm(p) for p in active_products(db)],
        company_kind=company.kind,
        amount=float(app.amount),
        term_months=app.term_months,
        purpose=app.purpose,
        has_collateral=app.has_collateral,
        owner_guarantee=app.owner_guarantee,
        declared_revenue=float(app.declared_revenue) if app.declared_revenue else None,
        revenue_confirmed=revenue_confirmed(app),
        info_rounds=app.info_rounds,
        key_rate=key_rate,
        as_of=as_of,
        avg_monthly_turnover=stats.avg_monthly,
        effective_monthly_turnover=effective_turnover(stats, app),
        existing_monthly_payments=existing_payments(db, app, sources),
        bank_tenure_months=months_between(company.client_since, as_of),
        business_age_months=months_between(company.registration_date, as_of),
        scoring=scoring,
        egrul=sources.get("EGRUL", {}),
        fns=sources.get("FNS", {}),
        bki=sources.get("BKI", {}),
        documents={d.doc_type for d in app.documents},
        registration_date=company.registration_date,
        prior_manual_decline=prior_manual_decline(db, company.id, app.id, as_of),
    )


# --------------------------------------------------------------------------- подбор продукта


def known_registry_status(db: Session, company_id) -> str | None:
    """Статус компании по последней выписке из ЕГРЮЛ/ЕГРИП, которую банк получал по её заявкам."""
    payload = db.execute(
        select(EnrichmentResult.payload)
        .join(Application, Application.id == EnrichmentResult.application_id)
        .where(Application.company_id == company_id, EnrichmentResult.source == "EGRUL")
        .order_by(EnrichmentResult.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return payload.get("status") if isinstance(payload, dict) else None


def preapproved_limit(db: Session, company: Company, key_rate: float, as_of: date) -> dict | None:
    """Предварительный лимит экспресс-кредита по внутренним данным банка (обороты по счёту).

    Консервативно: рейтинг принимается равным C, долговая нагрузка в других банках неизвестна
    до запроса в БКИ, поэтому лимит предварительный — окончательный рассчитывается при рассмотрении.
    Если по последней выписке из реестра компания не действующая (ликвидация, банкротство), лимита нет:
    банк уже знает о стоп-факторе и не обещает кредит, в котором откажет.
    """
    product = db.get(Product, "EXPRESS")
    if product is None or not product.is_active:
        return None
    status = known_registry_status(db, company.id)
    if status is not None and status != "ACTIVE":
        return None
    stats = company_turnover(db, company.id)
    if stats.months < 3:
        return None
    params = ProductParams.from_orm(product)
    rate, _ = price(params, "C", key_rate, False, False, months_between(company.client_since, as_of))
    # платежи по кредитам этого банка известны и без БКИ — сразу уменьшают предварительный лимит
    limit, _ = max_limit(params, "C", rate, params.max_term, stats.avg_monthly, own_obligations(db, company.id))
    limit = floor_to(limit, 50_000)
    if limit < params.min_amount:
        return None
    return {
        "product_code": params.code,
        "product_name": params.name,
        "amount": limit,
        "max_term": params.max_term,
        # «ставка от» — лучшая ставка для этого клиента: рейтинг A и скидка за стаж в банке
        "rate_from": price(params, "A", key_rate, False, False, months_between(company.client_since, as_of))[0],
        "note": "Предварительно, по оборотам на вашем счёте. Окончательные условия — после рассмотрения заявки.",
    }


def match_products(
    db: Session,
    company: Company,
    purpose: str,
    amount: float,
    term_months: int,
    key_rate: float,
    as_of: date,
    has_collateral: bool | None = None,
    owner_guarantee: bool = False,
) -> list[dict]:
    """Подбор продуктов. Залог и поручительство — если клиент их уже указал (расчёт в черновике)."""
    stats = company_turnover(db, company.id)
    tenure = months_between(company.client_since, as_of)
    own = own_obligations(db, company.id)
    results = []
    for p in active_products(db):
        params = ProductParams.from_orm(p)
        if purpose not in params.purposes:
            continue
        term = min(max(term_months, params.min_term), params.max_term)
        pledge = params.collateral == "REQUIRED" if has_collateral is None else pledged(p, has_collateral)
        rate_best, _ = price(params, "A", key_rate, pledge, owner_guarantee, tenure)
        rate_typical, _ = price(params, "B", key_rate, pledge, owner_guarantee, tenure)
        rate_worst, _ = price(params, "C", key_rate, pledge, owner_guarantee, tenure)
        limit, _ = max_limit(params, "B", rate_typical, term, stats.avg_monthly, own)
        limit = min(limit, int(params.max_amount))
        fit_amount = min(max(amount, params.min_amount), params.max_amount)
        monthly, interest = offer_economics(fit_amount, rate_typical, term, params.repayment_type)

        notes = []
        fit = "full"
        if amount < params.min_amount:
            notes.append(f"Минимальная сумма продукта — {fmt_money(params.min_amount)}")
            fit = "partial"
        if amount > params.max_amount:
            notes.append(f"Максимальная сумма продукта — {fmt_money(params.max_amount)}")
            fit = "partial"
        if term != term_months:
            notes.append(f"Срок по продукту: {months_range(params.min_term, params.max_term)}")
            fit = "partial"
        if limit and fit_amount > limit:
            notes.append(
                f"По оборотам на счёте предварительно доступно до {fmt_money(limit)} — итог зависит от проверки"
            )
            fit = "partial"
        docs = required_documents(p, fit_amount, purpose, company.kind)
        results.append(
            {
                "product_code": p.code,
                "product_name": p.name,
                "tagline": p.tagline,
                "features": p.features,
                "fit": fit,
                "notes": notes,
                "amount": fit_amount,
                "term_months": term,
                "rate_from": rate_best,
                "rate_to": rate_worst,
                "rate_typical": rate_typical,
                "monthly_payment": monthly,
                "total_interest": interest,
                "repayment_type": p.repayment_type,
                "preliminary_limit": limit,
                "required_documents": docs,
                "collateral": p.collateral,
                "primary_purpose": params.purposes[0] == purpose,
            }
        )
    order = {"full": 0, "partial": 1}
    results.sort(key=lambda r: (order[r["fit"]], not r["primary_purpose"], r["rate_typical"]))
    if results:
        results[0]["recommended"] = True
    return results


__all__ = [
    "GRADE_LIMIT_FACTOR",
    "Purpose",
    "active_products",
    "build_policy_input",
    "build_scoring_input",
    "company_turnover",
    "effective_turnover",
    "enrichment",
    "match_products",
    "preapproved_limit",
    "revenue_confirmed",
    "scoring_from_row",
]
