"""Сборка входных данных для скоринга и политики из БД, подбор продукта, предодобренный лимит."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import DocType, Purpose
from app.domain.finance import floor_to, offer_economics
from app.domain.policy import (
    GRADE_LIMIT_FACTOR,
    STATEMENT_HAIRCUT,
    PolicyInput,
    ProductParams,
    indicative_rate,
    max_limit,
    price,
)
from app.domain.products import required_documents
from app.domain.scoring import Factor, ScoringInput, ScoringOutcome, TurnoverStats, months_between, turnover_stats
from app.models import AccountTurnover, Application, Company, EnrichmentResult, Product, ScoringResult


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
    """Если клиент подтвердил выручку выпиской из другого банка — учитываем её с дисконтом."""
    base = stats.avg_monthly
    if revenue_confirmed(app) and app.declared_revenue:
        base = max(base, float(app.declared_revenue) / 12 * STATEMENT_HAIRCUT)
    return base


def active_products(db: Session) -> list[Product]:
    return list(db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.sort_order)))


def build_scoring_input(
    app: Application, stats: TurnoverStats, sources: dict[str, dict], key_rate: float, as_of: date
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
        has_collateral=app.has_collateral or app.product.collateral == "REQUIRED",
        owner_guarantee=app.owner_guarantee,
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
        existing_monthly_payments=float(sources.get("BKI", {}).get("total_monthly_payment", 0) or 0),
        bank_tenure_months=months_between(company.client_since, as_of),
        business_age_months=months_between(company.registration_date, as_of),
        scoring=scoring,
        egrul=sources.get("EGRUL", {}),
        fns=sources.get("FNS", {}),
        bki=sources.get("BKI", {}),
    )


# --------------------------------------------------------------------------- подбор продукта


def preapproved_limit(db: Session, company: Company, key_rate: float, as_of: date) -> dict | None:
    """Предварительный лимит экспресс-кредита по внутренним данным банка (обороты по счёту).

    Консервативно: рейтинг принимается равным C, долговая нагрузка в других банках неизвестна
    до запроса в БКИ, поэтому лимит предварительный — окончательный рассчитывается при рассмотрении.
    """
    product = db.get(Product, "EXPRESS")
    if product is None or not product.is_active:
        return None
    stats = company_turnover(db, company.id)
    if stats.months < 3:
        return None
    params = ProductParams.from_orm(product)
    rate, _ = price(params, "C", key_rate, False, False, months_between(company.client_since, as_of))
    limit, _ = max_limit(params, "C", rate, params.max_term, stats.avg_monthly, 0.0)
    limit = floor_to(limit, 50_000)
    if limit < params.min_amount:
        return None
    return {
        "product_code": params.code,
        "product_name": params.name,
        "amount": limit,
        "max_term": params.max_term,
        "rate_from": indicative_rate(params, key_rate, "A"),
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
) -> list[dict]:
    stats = company_turnover(db, company.id)
    tenure = months_between(company.client_since, as_of)
    results = []
    for p in active_products(db):
        params = ProductParams.from_orm(p)
        if purpose not in params.purposes:
            continue
        term = min(max(term_months, params.min_term), params.max_term)
        rate_best, _ = price(params, "A", key_rate, params.collateral == "REQUIRED", False, tenure)
        rate_typical, _ = price(params, "B", key_rate, params.collateral == "REQUIRED", False, tenure)
        rate_worst, _ = price(params, "C", key_rate, params.collateral == "REQUIRED", False, tenure)
        limit, _ = max_limit(params, "B", rate_typical, term, stats.avg_monthly, 0.0)
        limit = min(limit, int(params.max_amount))
        fit_amount = min(max(amount, params.min_amount), params.max_amount)
        monthly, interest = offer_economics(fit_amount, rate_typical, term, params.repayment_type)

        notes = []
        fit = "full"
        if amount < params.min_amount:
            notes.append(f"Минимальная сумма продукта — {params.min_amount:,.0f} ₽".replace(",", " "))
            fit = "partial"
        if amount > params.max_amount:
            notes.append(f"Максимальная сумма продукта — {params.max_amount:,.0f} ₽".replace(",", " "))
            fit = "partial"
        if term != term_months:
            notes.append(f"Срок по продукту: {params.min_term}–{params.max_term} мес.")
            fit = "partial"
        if limit and fit_amount > limit:
            notes.append(
                f"По оборотам на счёте предварительно доступно до {limit:,.0f} ₽ — итог зависит от проверки".replace(",", " ")
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
