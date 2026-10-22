"""Кредитная политика: из оценки риска получается решение.

Порядок правил:
1. Стоп-факторы → отказ.
2. Противоречия в данных → запрос информации у клиента (не более двух раундов).
3. Ставка = ключевая ставка + надбавка продукта + надбавка за риск − скидки.
4. Максимальный лимит = min(лимит продукта, кратность оборотов, платёжеспособность) × коэффициент рейтинга.
5. Если запрошенная сумма не проходит, подбираются встречные варианты:
   больший срок → меньшая сумма → другой продукт под ту же цель.
6. Триггеры ручного рассмотрения → заявка уходит аналитику вместе с рекомендацией системы.
   Иначе решение принимается автоматически.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from app.domain.enums import DecisionOutcome
from app.domain.finance import annuity_principal, floor_to, monthly_rate, offer_economics
from app.domain.products import NICE_TERMS
from app.domain.scoring import ScoringOutcome, months_between

POLICY_VERSION = "credit-policy-1.0"
DTI_CAP = 0.40  # все платежи по кредитам — не более 40% месячных поступлений
GRADE_LIMIT_FACTOR = {"A": 1.0, "B": 0.9, "C": 0.75, "D": 0.6, "E": 0.0}
GRADE_PREMIUM = {"A": 0.0, "B": 1.0, "C": 2.5, "D": 4.0, "E": 6.0}
REVENUE_MISMATCH_RATIO = 1.6  # заявленная выручка больше годовых поступлений на счёт в 1,6 раза
STATEMENT_HAIRCUT = 0.8  # подтверждённая выпиской выручка учитывается с дисконтом 20%
TAX_ARREARS_MANUAL = 300_000


@dataclass
class ProductParams:
    code: str
    name: str
    purposes: list[str]
    min_amount: float
    max_amount: float
    min_term: int
    max_term: int
    base_spread: float
    max_spread: float
    turnover_multiplier: float
    auto_decision_limit: float
    repayment_type: str
    collateral: str

    @classmethod
    def from_orm(cls, p) -> ProductParams:
        return cls(
            code=p.code,
            name=p.name,
            purposes=list(p.purposes),
            min_amount=float(p.min_amount),
            max_amount=float(p.max_amount),
            min_term=p.min_term,
            max_term=p.max_term,
            base_spread=float(p.base_spread),
            max_spread=float(p.max_spread),
            turnover_multiplier=float(p.turnover_multiplier),
            auto_decision_limit=float(p.auto_decision_limit),
            repayment_type=p.repayment_type,
            collateral=p.collateral,
        )


@dataclass
class Reason:
    code: str
    kind: str  # STOP / MANUAL / INFO / LIMIT / NOTE / POSITIVE
    text: str  # для аналитика
    client_text: str  # для клиента

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OfferDraft:
    product_code: str
    product_name: str
    amount: float
    term_months: int
    rate: float
    monthly_payment: float
    total_interest: float
    repayment_type: str
    conditions: list[str]
    pricing: dict
    is_counter: bool
    label: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyInput:
    product: ProductParams
    alt_products: list[ProductParams]
    company_kind: str
    amount: float
    term_months: int
    purpose: str
    has_collateral: bool
    owner_guarantee: bool
    declared_revenue: float | None
    revenue_confirmed: bool  # клиент приложил выписку из другого банка
    info_rounds: int
    key_rate: float
    as_of: date
    avg_monthly_turnover: float
    effective_monthly_turnover: float
    existing_monthly_payments: float
    bank_tenure_months: int
    business_age_months: int
    scoring: ScoringOutcome
    egrul: dict
    fns: dict
    bki: dict


@dataclass
class PolicyResult:
    outcome: str
    reasons: list[Reason]
    offers: list[OfferDraft] = field(default_factory=list)
    info_items: list[dict] = field(default_factory=list)
    recommendation: str | None = None  # что система рекомендует аналитику при ручном рассмотрении
    details: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- ценообразование и лимит


def price(
    product: ProductParams,
    grade: str,
    key_rate: float,
    has_collateral: bool,
    owner_guarantee: bool,
    tenure_months: int,
) -> tuple[float, list[dict]]:
    parts = [
        {"label": "Ключевая ставка Банка России", "value": key_rate},
        {"label": f"Надбавка продукта «{product.name}»", "value": product.base_spread},
        {"label": f"Надбавка за риск (рейтинг {grade})", "value": GRADE_PREMIUM[grade]},
    ]
    if has_collateral or product.collateral == "REQUIRED":
        parts.append({"label": "Скидка за залог", "value": -1.0})
    if owner_guarantee:
        parts.append({"label": "Скидка за поручительство собственника", "value": -0.5})
    if tenure_months >= 24:
        parts.append({"label": "Скидка клиенту банка дольше 2 лет", "value": -0.5})
    raw = sum(p["value"] for p in parts)
    rate = min(max(raw, key_rate + 1.0), key_rate + product.max_spread)
    rate = round(rate, 1)
    if rate != round(raw, 1):
        parts.append({"label": "Ограничение диапазона ставок продукта", "value": round(rate - raw, 2)})
    return rate, parts


def max_limit(
    product: ProductParams,
    grade: str,
    rate: float,
    term: int,
    effective_turnover: float,
    existing_payments: float,
) -> tuple[int, dict]:
    by_turnover = effective_turnover * product.turnover_multiplier
    capacity = DTI_CAP * effective_turnover - existing_payments
    if product.repayment_type == "REVOLVING":
        # по овердрафту ежемесячно платятся только проценты
        by_capacity = capacity / monthly_rate(rate) if capacity > 0 else 0.0
    else:
        by_capacity = annuity_principal(capacity, rate, term)
    factor = GRADE_LIMIT_FACTOR[grade]
    base = min(product.max_amount, by_turnover, by_capacity)
    limit = floor_to(base * factor, 10_000)
    calc = {
        "term_months": term,
        "rate": rate,
        "product_max": product.max_amount,
        "by_turnover": round(by_turnover, 2),
        "turnover_multiplier": product.turnover_multiplier,
        "payment_capacity": round(capacity, 2),
        "by_payment_capacity": round(by_capacity, 2),
        "grade_factor": factor,
        "max_limit": limit,
    }
    return limit, calc


def _conditions(product: ProductParams, amount: float, owner_guarantee: bool) -> list[str]:
    conds: list[str] = []
    if product.collateral == "REQUIRED":
        conds.append("Залог приобретаемого имущества")
    if owner_guarantee or (product.code == "WORKING_CAPITAL" and amount > 10_000_000):
        conds.append("Поручительство собственника бизнеса")
    if product.repayment_type == "REVOLVING":
        conds.append("Погашение поступлениями на расчётный счёт")
        conds.append("Лимит действует 12 месяцев")
    else:
        conds.append("Ежемесячные равные платежи, досрочное погашение без комиссии")
    return conds


def _offer(
    product: ProductParams,
    amount: float,
    term: int,
    rate: float,
    pricing: list[dict],
    owner_guarantee: bool,
    is_counter: bool,
    label: str,
) -> OfferDraft:
    monthly, interest = offer_economics(amount, rate, term, product.repayment_type)
    return OfferDraft(
        product_code=product.code,
        product_name=product.name,
        amount=float(amount),
        term_months=term,
        rate=rate,
        monthly_payment=monthly,
        total_interest=interest,
        repayment_type=product.repayment_type,
        conditions=_conditions(product, amount, owner_guarantee),
        pricing={"parts": pricing, "rate": rate},
        is_counter=is_counter,
        label=label,
    )


def _rub(v: float) -> str:
    """12345678 → «12 345 678 ₽» (неразрывные пробелы между разрядами)."""
    return f"{v:,.0f}".replace(",", "\u00a0") + "\u00a0₽"


# --------------------------------------------------------------------------- правила


def stop_factors(inp: PolicyInput) -> list[Reason]:
    reasons: list[Reason] = []
    status = inp.egrul.get("status", "ACTIVE")
    if status != "ACTIVE":
        status_text = inp.egrul.get("status_text", status).lower()
        reasons.append(
            Reason(
                "company_status",
                "STOP",
                f"Статус по ЕГРЮЛ/ЕГРИП: {status_text}",
                f"По данным ЕГРЮЛ компания находится в статусе «{status_text}»",
            )
        )
    overdue12 = int(inp.bki.get("max_overdue_days_12m", 0) or 0)
    current = int(inp.bki.get("current_overdue_days", 0) or 0)
    if overdue12 > 90 or current > 90:
        reasons.append(
            Reason(
                "bki_overdue_90",
                "STOP",
                f"Просрочка свыше 90 дней (за 12 мес.: {overdue12} дн., текущая: {current} дн.)",
                "В кредитной истории есть просрочка более 90 дней",
            )
        )
    if inp.business_age_months < 3:
        reasons.append(
            Reason(
                "business_too_young",
                "STOP",
                f"Бизнес зарегистрирован {inp.business_age_months} мес. назад (минимум 3)",
                "Бизнес работает меньше трёх месяцев",
            )
        )
    if inp.scoring.grade == "E":
        reasons.append(
            Reason(
                "grade_e",
                "STOP",
                f"Рейтинг E (балл {inp.scoring.score}): риск выше допустимого",
                "Оценка кредитоспособности ниже минимально допустимой",
            )
        )
    return reasons


def info_checks(inp: PolicyInput) -> list[dict]:
    items: list[dict] = []
    if inp.declared_revenue and not inp.revenue_confirmed and inp.avg_monthly_turnover > 0:
        annual = inp.avg_monthly_turnover * 12
        ratio = inp.declared_revenue / annual
        if ratio > REVENUE_MISMATCH_RATIO:
            items.append(
                {
                    "code": "revenue_statement",
                    "type": "DOCUMENT",
                    "doc_type": "BANK_STATEMENT",
                    "label": "Выписка по счёту в другом банке за последние 6–12 месяцев",
                    "why": (
                        f"Указанная выручка {_rub(inp.declared_revenue)} в год в {ratio:.1f}".replace(".", ",")
                        + f" раза больше поступлений на ваш счёт в нашем банке ({_rub(annual)} за 12 месяцев)"
                    ),
                }
            )
            items.append(
                {
                    "code": "revenue_explanation",
                    "type": "COMMENT",
                    "label": "Поясните, куда поступает остальная выручка",
                    "why": "Это поможет учесть всю выручку бизнеса при расчёте суммы кредита",
                }
            )
    return items


def manual_triggers(inp: PolicyInput, offers: list[OfferDraft], unresolved_info: bool) -> list[Reason]:
    reasons: list[Reason] = []
    top_amount = max([o.amount for o in offers], default=inp.amount)
    product_by_code = {p.code: p for p in [inp.product, *inp.alt_products]}
    for o in offers:
        p = product_by_code[o.product_code]
        if o.amount > p.auto_decision_limit:
            reasons.append(
                Reason(
                    "above_auto_limit",
                    "MANUAL",
                    f"Сумма {o.amount:,.0f} ₽ выше лимита автоматического решения "
                    f"по продукту «{p.name}» ({p.auto_decision_limit:,.0f} ₽)".replace(",", " "),
                    "Для такой суммы решение принимает кредитный специалист",
                )
            )
            break
    else:
        if not offers and top_amount > inp.product.auto_decision_limit:
            reasons.append(Reason("above_auto_limit", "MANUAL", "Сумма выше лимита автоматического решения", ""))
    if inp.scoring.grade == "D":
        reasons.append(
            Reason(
                "grade_d",
                "MANUAL",
                f"Рейтинг D (балл {inp.scoring.score}) — повышенный риск, нужна экспертная оценка",
                "Заявке нужна дополнительная экспертная оценка",
            )
        )
    overdue12 = int(inp.bki.get("max_overdue_days_12m", 0) or 0)
    if 30 < overdue12 <= 90:
        reasons.append(
            Reason(
                "bki_overdue_31_90",
                "MANUAL",
                f"Просрочка {overdue12} дн. за последние 12 месяцев",
                "Специалист уточнит обстоятельства просрочки в кредитной истории",
            )
        )
    if inp.fns.get("suspended_accounts"):
        reasons.append(Reason("suspended_accounts", "MANUAL", "ФНС приостанавливала операции по счетам", ""))
    if inp.fns.get("unreliable_data"):
        reasons.append(Reason("unreliable_data", "MANUAL", "В ЕГРЮЛ есть отметка о недостоверности сведений", ""))
    if inp.fns.get("mass_address"):
        reasons.append(Reason("mass_address", "MANUAL", "Адрес массовой регистрации", ""))
    arrears = float(inp.fns.get("tax_arrears", 0) or 0)
    if arrears > TAX_ARREARS_MANUAL:
        reasons.append(
            Reason(
                "tax_arrears",
                "MANUAL",
                f"Задолженность по налогам {arrears:,.0f} ₽".replace(",", " "),
                "",
            )
        )
    director_since = inp.egrul.get("director", {}).get("since")
    if director_since and months_between(date.fromisoformat(director_since), inp.as_of) < 6:
        reasons.append(Reason("director_changed", "MANUAL", f"Смена руководителя {director_since}", ""))
    if unresolved_info:
        reasons.append(
            Reason(
                "info_unresolved",
                "MANUAL",
                "Расхождение заявленной выручки и оборотов не устранено после запросов",
                "Специалист проверит предоставленные документы",
            )
        )
    return reasons


def _positive_reasons(scoring: ScoringOutcome) -> list[Reason]:
    best = sorted([f for f in scoring.factors if f.points > 0], key=lambda f: -f.points)[:3]
    return [Reason(f.code, "POSITIVE", f.comment, f.client_text or f.comment) for f in best]


def evaluate(inp: PolicyInput) -> PolicyResult:
    details: dict = {
        "policy_version": POLICY_VERSION,
        "dti_cap": DTI_CAP,
        "grade": inp.scoring.grade,
        "score": inp.scoring.score,
        "key_rate": inp.key_rate,
        "effective_monthly_turnover": round(inp.effective_monthly_turnover, 2),
        "existing_monthly_payments": round(inp.existing_monthly_payments, 2),
    }

    stops = stop_factors(inp)
    if stops:
        details["stop_factors"] = [r.code for r in stops]
        return PolicyResult(DecisionOutcome.DECLINE, stops, details=details)

    info_items = info_checks(inp)
    unresolved_info = False
    if info_items:
        if inp.info_rounds < 2:
            reasons = [
                Reason(
                    "revenue_mismatch",
                    "INFO",
                    "Заявленная выручка существенно выше поступлений на счёт",
                    "Нужно подтвердить выручку, чтобы учесть её при расчёте суммы",
                )
            ]
            return PolicyResult(DecisionOutcome.NEEDS_INFO, reasons, info_items=info_items, details=details)
        unresolved_info = True

    product = inp.product
    rate, pricing = price(
        product, inp.scoring.grade, inp.key_rate, inp.has_collateral, inp.owner_guarantee, inp.bank_tenure_months
    )
    limit, calc = max_limit(
        product, inp.scoring.grade, rate, inp.term_months, inp.effective_monthly_turnover, inp.existing_monthly_payments
    )
    details["limit_calculation"] = calc
    details["pricing"] = pricing

    offers: list[OfferDraft] = []
    reasons: list[Reason] = []
    requested_fits = inp.amount <= limit

    if requested_fits:
        offers.append(
            _offer(
                product, inp.amount, inp.term_months, rate, pricing, inp.owner_guarantee, False, "Запрошенные условия"
            )
        )
    else:
        reasons.append(
            Reason(
                "limit_exceeded",
                "LIMIT",
                f"Запрошено {inp.amount:,.0f} ₽, максимальный лимит на срок {inp.term_months} мес. — {limit:,.0f} ₽".replace(
                    ",", " "
                ),
                "Запрошенная сумма выше, чем позволяют обороты и текущая долговая нагрузка: "
                "платежи по всем кредитам не должны превышать 40% поступлений на счёт",
            )
        )
        # Вариант А: та же сумма на больший срок
        if product.repayment_type == "ANNUITY":
            for t in NICE_TERMS:
                if t <= inp.term_months or t < product.min_term or t > product.max_term:
                    continue
                lim_t, _ = max_limit(
                    product, inp.scoring.grade, rate, t, inp.effective_monthly_turnover, inp.existing_monthly_payments
                )
                if inp.amount <= lim_t:
                    offers.append(
                        _offer(
                            product, inp.amount, t, rate, pricing, inp.owner_guarantee, True, f"Вся сумма на {t} мес."
                        )
                    )
                    break
        # Вариант Б: меньшая сумма на тот же срок
        if limit >= product.min_amount:
            offers.append(
                _offer(
                    product,
                    limit,
                    inp.term_months,
                    rate,
                    pricing,
                    inp.owner_guarantee,
                    True,
                    "Меньшая сумма на тот же срок",
                )
            )
        # Вариант В: другой продукт под ту же цель
        if not offers:
            for alt in inp.alt_products:
                if alt.code == product.code or inp.purpose not in alt.purposes:
                    continue
                alt_rate, alt_pricing = price(
                    alt,
                    inp.scoring.grade,
                    inp.key_rate,
                    inp.has_collateral,
                    inp.owner_guarantee,
                    inp.bank_tenure_months,
                )
                alt_term = min(max(inp.term_months, alt.min_term), alt.max_term)
                alt_limit, _ = max_limit(
                    alt,
                    inp.scoring.grade,
                    alt_rate,
                    alt_term,
                    inp.effective_monthly_turnover,
                    inp.existing_monthly_payments,
                )
                amount = min(inp.amount, alt_limit)
                if amount >= alt.min_amount:
                    offers.append(
                        _offer(
                            alt,
                            amount,
                            alt_term,
                            alt_rate,
                            alt_pricing,
                            inp.owner_guarantee,
                            True,
                            f"Другой продукт: {alt.name}",
                        )
                    )
                    break
        if not offers:
            reasons.append(
                Reason(
                    "no_capacity",
                    "STOP",
                    "Платёжеспособность не позволяет обслуживать даже минимальную сумму",
                    "Текущие обороты и долговая нагрузка не позволяют обслуживать новый кредит",
                )
            )
            return PolicyResult(DecisionOutcome.DECLINE, reasons, details=details)

    manual = manual_triggers(inp, offers, unresolved_info)
    reasons.extend(_positive_reasons(inp.scoring))
    automatic_outcome = DecisionOutcome.APPROVE if requested_fits else DecisionOutcome.COUNTER_OFFER
    details["offers"] = [o.to_dict() for o in offers]

    if manual:
        details["manual_triggers"] = [r.code for r in manual]
        return PolicyResult(
            DecisionOutcome.MANUAL_REVIEW,
            manual + reasons,
            offers=offers,
            recommendation=automatic_outcome,
            details=details,
        )
    return PolicyResult(automatic_outcome, reasons, offers=offers, details=details)


def indicative_rate(product: ProductParams, key_rate: float, grade: str = "B") -> float:
    """Ориентировочная ставка до скоринга — для подбора продукта и расчёта нагрузки."""
    rate, _ = price(product, grade, key_rate, product.collateral == "REQUIRED", False, 0)
    return rate
