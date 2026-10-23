"""Конструктор условий: каким будет решение банка при другой сумме, сроке, залоге и поручительстве.

Расчёт идёт той же балльной моделью и той же кредитной политикой, что и решение по заявке, — на
сведениях из ЕГРЮЛ, ФНС и БКИ, полученных при её рассмотрении, и на оборотах по счёту. В базу ничего
не записывается: это ответ на вопрос «что, если», а не новое решение.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import DOC_TYPES, AppStatus, DecisionKind, DecisionOutcome, OfferStatus
from app.domain.finance import floor_to, offer_economics
from app.domain.policy import DTI_CAP, ProductParams, evaluate, price
from app.domain.products import NICE_TERMS, pledged, required_documents
from app.domain.scoring import months_between, score
from app.domain.text import fmt_amount, fmt_money, fmt_rate, lower_first, months_range
from app.models import Application, Decision, Offer, Product
from app.services import assessment
from app.services.workflow import key_rate, local_today, utcnow

# Конструктор открыт там, где заявка уже оценена: есть рейтинг и сведения из источников
SIMULATION_STATUSES = {
    AppStatus.MANUAL_REVIEW,
    AppStatus.OFFER_READY,
    AppStatus.SIGNING,
    AppStatus.DECLINED,
    AppStatus.CLIENT_DECLINED,
    AppStatus.EXPIRED,
    AppStatus.CANCELLED,
}
REQUIRED_SOURCES = ("EGRUL", "FNS", "BKI")

# Чем исход лучше для клиента, тем больше число
OUTCOME_RANK = {
    DecisionOutcome.DECLINE: 0,
    DecisionOutcome.NEEDS_INFO: 1,
    DecisionOutcome.MANUAL_REVIEW: 2,
    DecisionOutcome.COUNTER_OFFER: 3,
    DecisionOutcome.APPROVE: 4,
}
VERDICTS = {
    DecisionOutcome.APPROVE: "Одобрим автоматически",
    DecisionOutcome.COUNTER_OFFER: "Предложим другие условия",
    DecisionOutcome.MANUAL_REVIEW: "Решение примет кредитный специалист",
    DecisionOutcome.NEEDS_INFO: "Попросим подтвердить выручку",
    DecisionOutcome.DECLINE: "Не сможем одобрить",
}


class SimulationError(Exception):
    """Конструктор недоступен (unavailable=True → 409) или параметры вне условий продукта (→ 422)."""

    def __init__(self, message: str, unavailable: bool = False):
        super().__init__(message)
        self.unavailable = unavailable


@dataclass
class Params:
    product_code: str
    amount: float
    term_months: int
    has_collateral: bool
    owner_guarantee: bool

    def to_dict(self) -> dict:
        return {
            "product_code": self.product_code,
            "amount": self.amount,
            "term_months": self.term_months,
            "has_collateral": self.has_collateral,
            "owner_guarantee": self.owner_guarantee,
        }


def _rub(v: float) -> str:
    return fmt_money(v)


def _pct(v: float) -> str:
    return fmt_rate(v)


class Simulator:
    """Один экземпляр на запрос: данные заявки читаются один раз, сценариев можно посчитать сколько угодно."""

    def __init__(self, db: Session, app: Application):
        if AppStatus(app.status) not in SIMULATION_STATUSES:
            raise SimulationError("Конструктор условий доступен после того, как банк оценит заявку", True)
        self.db = db
        self.app = app
        last = db.scalar(
            select(Decision).where(Decision.application_id == app.id).order_by(Decision.created_at.desc()).limit(1)
        )
        if last is not None and last.kind == DecisionKind.MANUAL and last.outcome == DecisionOutcome.DECLINE:
            # решение специалиста не пересчитывается автоматически — вопросы можно задать ему в переписке
            raise SimulationError("По заявке решение принял кредитный специалист — условия подбирает он", True)
        if last is not None and any(r.get("code") == "technical_failure" for r in last.reasons or []):
            # автоматическая оценка не завершилась из-за сбоя — тот же расчёт в конструкторе не поможет
            raise SimulationError("Заявку проверяет специалист: автоматическая оценка не завершилась", True)
        self.sources = assessment.enrichment(db, app.id)
        if not all(self.sources.get(s) for s in REQUIRED_SOURCES):
            raise SimulationError("Конструктор условий доступен после того, как банк оценит заявку", True)
        self.stats = assessment.company_turnover(db, app.company_id)
        self.key_rate = key_rate(db)
        self.today = local_today()
        self.tenure = months_between(app.company.client_since, self.today)
        self.products = {p.code: p for p in assessment.active_products(db)}
        self.own_payments = assessment.own_obligations(db, app.company_id, app.id)
        self.doc_types = {d.doc_type for d in app.documents}
        # действующие предложения банка: те же условия клиент выбирает в предложении, без новой заявки
        now = utcnow()
        self.offers = (
            [
                o
                for o in db.scalars(
                    select(Offer).where(Offer.application_id == app.id, Offer.status == OfferStatus.ACTIVE)
                )
                if o.valid_until > now
            ]
            if app.status == AppStatus.OFFER_READY
            else []
        )
        if self.offers:
            room = assessment.load_room(db, app, db.get(Decision, self.offers[0].decision_id))
            if room is not None:
                # после решения у компании появились кредиты: вариант, который не помещается в нагрузку,
                # принять нельзя — такие условия конструктор считает заново, как для новой заявки
                self.offers = [o for o in self.offers if room.fits(float(o.monthly_payment))]

    # ------------------------------------------------------------------ параметры

    def fitting_products(self) -> list[Product]:
        """Продукты, подходящие под цель заявки."""
        return [p for p in self.products.values() if self.app.purpose in p.purposes]

    def product_options(self) -> list[dict]:
        """Границы для элементов управления: продукты под цель заявки, их суммы и сроки."""
        return [
            {
                "code": p.code,
                "name": p.name,
                "min_amount": float(p.min_amount),
                "max_amount": float(p.max_amount),
                "min_term": p.min_term,
                "max_term": p.max_term,
                "terms": [t for t in NICE_TERMS if p.min_term <= t <= p.max_term] or [p.min_term],
                "collateral": p.collateral,
                "repayment_type": p.repayment_type,
                "auto_decision_limit": float(p.auto_decision_limit),
            }
            for p in self.fitting_products()
        ]

    def base_params(self) -> Params:
        product = self.products.get(self.app.product_code) or self.db.get(Product, self.app.product_code)
        return Params(
            product_code=self.app.product_code,
            amount=float(self.app.amount),
            term_months=self.app.term_months,
            has_collateral=pledged(product, self.app.has_collateral),
            owner_guarantee=bool(self.app.owner_guarantee),
        )

    def params(self, product_code=None, amount=None, term_months=None, has_collateral=None, owner_guarantee=None):
        """Условия из запроса: не переданное берётся из заявки."""
        base = self.base_params()
        return Params(
            product_code=product_code or base.product_code,
            amount=amount if amount is not None else base.amount,
            term_months=term_months or base.term_months,
            has_collateral=base.has_collateral if has_collateral is None else has_collateral,
            owner_guarantee=base.owner_guarantee if owner_guarantee is None else owner_guarantee,
        )

    def matching_offer(self, p: Params) -> Offer | None:
        """Действующее предложение банка ровно на этих условиях."""
        product = self.products.get(p.product_code)
        if product is None or p.owner_guarantee != bool(self.app.owner_guarantee):
            return None
        if pledged(product, p.has_collateral) != pledged(product, self.app.has_collateral):
            return None
        for o in self.offers:
            if (
                o.product_code == p.product_code
                and o.term_months == p.term_months
                and abs(float(o.amount) - p.amount) < 0.5
            ):
                return o
        return None

    def todo(self, product: Product, p: Params) -> list[str]:
        """Что останется сделать в новой заявке на этих условиях — кроме согласий и отправки."""
        items = []
        if pledged(product, p.has_collateral) and not (self.app.collateral_description or "").strip():
            items.append("описать имущество для залога")
        missing = [
            lower_first(DOC_TYPES.get(t, t))
            for t in required_documents(product, p.amount, self.app.purpose, self.app.company.kind)
            if t not in self.doc_types
        ]
        if missing:
            items.append("приложить " + ("документ" if len(missing) == 1 else "документы") + ": " + ", ".join(missing))
        return items

    def validate(self, p: Params) -> Product:
        product = self.products.get(p.product_code)
        if product is None:
            raise SimulationError("Продукт не найден")
        if self.app.purpose not in product.purposes:
            raise SimulationError(f"Продукт «{product.name}» не подходит для цели заявки")
        if not float(product.min_amount) <= p.amount <= float(product.max_amount):
            raise SimulationError(
                f"Сумма по продукту «{product.name}» — от {fmt_amount(product.min_amount)} "
                f"до {_rub(float(product.max_amount))}"
            )
        if not product.min_term <= p.term_months <= product.max_term:
            raise SimulationError(
                f"Срок по продукту «{product.name}» — {months_range(product.min_term, product.max_term)}"
            )
        return product

    # ------------------------------------------------------------------ расчёт

    def run(self, p: Params) -> dict:
        product = self.validate(p)
        collateral = pledged(product, p.has_collateral)
        # Заявка «как если бы» с другими параметрами: те же компания, цель, выручка и документы
        proxy = SimpleNamespace(
            id=self.app.id,
            company_id=self.app.company_id,
            product=product,
            product_code=product.code,
            company=self.app.company,
            documents=self.app.documents,
            amount=p.amount,
            term_months=p.term_months,
            purpose=self.app.purpose,
            has_collateral=collateral,
            owner_guarantee=p.owner_guarantee,
            declared_revenue=self.app.declared_revenue,
            info_rounds=self.app.info_rounds,
        )
        scoring = score(
            assessment.build_scoring_input(
                proxy, self.stats, self.sources, self.key_rate, self.today, self.own_payments
            )
        )
        inp = assessment.build_policy_input(
            self.db, proxy, scoring, self.sources, self.stats, self.key_rate, self.today
        )
        result = evaluate(inp)

        params = ProductParams.from_orm(product)
        rate, _ = price(params, scoring.grade, self.key_rate, collateral, p.owner_guarantee, self.tenure)
        monthly, interest = offer_economics(p.amount, rate, p.term_months, params.repayment_type)
        turnover = inp.effective_monthly_turnover
        existing = inp.existing_monthly_payments
        share = (existing + monthly) / turnover if turnover > 0 else None
        limit = result.details.get("limit_calculation", {}).get("max_limit")
        outcome = DecisionOutcome(result.outcome)

        if outcome == DecisionOutcome.APPROVE:
            explanation = "Решение — сразу после отправки заявки, без участия специалиста"
        elif outcome == DecisionOutcome.COUNTER_OFFER:
            explanation = (
                f"На {p.term_months} мес. можно до {_rub(limit)}: платежи по всем кредитам не должны "
                "превышать 40% поступлений на счёт"
                if limit
                else "Банк подберёт другие условия: платежи по всем кредитам не должны превышать 40% поступлений"
            )
        elif outcome == DecisionOutcome.MANUAL_REVIEW:
            codes = {r.code for r in result.reasons}
            explanation = (
                f"Сумма выше лимита автоматического решения ({_rub(float(product.auto_decision_limit))}) — "
                "специалист рассмотрит заявку в течение рабочего дня"
                if "above_auto_limit" in codes
                else "Нужна экспертиза специалиста — обычно это занимает рабочий день"
            )
        elif outcome == DecisionOutcome.NEEDS_INFO:
            explanation = "Указанная выручка выше поступлений на счёт — банк попросит выписку из другого банка"
        else:
            texts = [r.client_text for r in result.reasons if r.kind == "STOP" and r.client_text]
            explanation = texts[0] if texts else "Текущие обороты и долговая нагрузка не позволяют выдать кредит"
        # Отказ по стоп-фактору (статус компании, просрочки, рейтинг) другими условиями не исправить
        fixable = not any(r.kind == "STOP" and r.code != "no_capacity" for r in result.reasons)
        match = self.matching_offer(p)

        return {
            **p.to_dict(),
            "has_collateral": collateral,
            "product_name": product.name,
            "repayment_type": params.repayment_type,
            "collateral_option": product.collateral,
            "outcome": outcome.value,
            "verdict": VERDICTS[outcome],
            "explanation": explanation,
            "automatic": outcome != DecisionOutcome.MANUAL_REVIEW,
            "fixable": fixable,
            "grade": scoring.grade if outcome != DecisionOutcome.DECLINE else None,
            "rate": rate,
            "monthly_payment": monthly,
            "total_interest": interest,
            "max_amount": limit,
            "payment_share": round(share, 4) if share is not None else None,
            "payment_share_cap": DTI_CAP,
            "offers": [
                {
                    "label": o.label,
                    "product_code": o.product_code,
                    "product_name": o.product_name,
                    "amount": o.amount,
                    "term_months": o.term_months,
                    "rate": o.rate,
                    "monthly_payment": o.monthly_payment,
                    "is_counter": o.is_counter,
                }
                for o in result.offers
            ],
            "key_rate": self.key_rate,
            # условия совпадают с действующим предложением банка — его выбирают без новой заявки
            "offer": {
                "id": str(match.id),
                "label": match.label,
                "rate": float(match.rate),
                "monthly_payment": float(match.monthly_payment),
            }
            if match
            else None,
            "todo": self.todo(product, p),
        }

    # ------------------------------------------------------------------ подсказки

    def suggestions(self, base: Params, current: dict) -> list[dict]:
        """Что изменить, чтобы условия стали лучше: каждый вариант посчитан той же политикой."""
        product = self.products[base.product_code]
        candidates: list[tuple[str, str, Params]] = []
        if not base.owner_guarantee:
            candidates.append(("guarantee", "Поручительство собственника", _replace(base, owner_guarantee=True)))
        if product.collateral == "OPTIONAL" and not current["has_collateral"]:
            candidates.append(("collateral", "Залог имущества", _replace(base, has_collateral=True)))

        if product.repayment_type == "ANNUITY":
            longer = [t for t in NICE_TERMS if base.term_months < t <= product.max_term and t >= product.min_term]
            chosen, covered = None, False
            for t in longer:  # самый короткий срок, при котором решение становится лучше
                if self.matching_offer(_replace(base, term_months=t)):
                    # этот срок уже есть в предложении банка: его выбирают там, а более длинный срок
                    # был бы хуже готового варианта (та же сумма, но переплата больше)
                    covered = True
                    break
                trial = self.run(_replace(base, term_months=t))
                if OUTCOME_RANK[DecisionOutcome(trial["outcome"])] > OUTCOME_RANK[DecisionOutcome(current["outcome"])]:
                    chosen = t
                    break
            if chosen is None and not covered and longer:
                chosen = longer[min(1, len(longer) - 1)]  # иначе — на шаг-два длиннее: платёж ниже
            if chosen:
                candidates.append(("term", f"Срок {chosen} мес.", _replace(base, term_months=chosen)))

        cap = current.get("max_amount") or 0
        if current["outcome"] == DecisionOutcome.MANUAL_REVIEW and base.amount > float(product.auto_decision_limit):
            cap = min(cap or float(product.auto_decision_limit), float(product.auto_decision_limit))
        if cap and cap < base.amount:
            smaller = floor_to(cap, 10_000)
            if smaller >= float(product.min_amount):
                candidates.append(("amount", f"Сумма {_rub(smaller)}", _replace(base, amount=float(smaller))))

        for alt in self.fitting_products():
            if alt.code == base.product_code:
                continue
            amount = min(max(base.amount, float(alt.min_amount)), float(alt.max_amount))
            term = min(max(base.term_months, alt.min_term), alt.max_term)
            alt_params = Params(alt.code, amount, term, pledged(alt, base.has_collateral), base.owner_guarantee)
            if self.todo(alt, alt_params):
                # как и во встречном предложении банка: другой продукт — только если для него
                # не нужны залог и документы, которых нет в заявке
                continue
            title = f"«{alt.name}»"
            if amount != base.amount or term != base.term_months:
                title += f": {_rub(amount)} на {term} мес."
            if alt.collateral == "REQUIRED":
                title += ", с залогом приобретаемого имущества"
            candidates.append((f"product:{alt.code}", title, alt_params))

        found: list[dict] = []
        for code, title, params in candidates:
            if self.matching_offer(params):
                continue  # это уже есть в предложении банка — выбрать его можно без новой заявки
            try:
                res = self.run(params)
            except SimulationError:
                continue
            effect = _effect(current, res)
            if effect is None:
                continue
            found.append({"code": code, "title": title, "effect": effect, "params": params.to_dict(), "result": res})

        # лучшие — где решение лучше, затем где ниже ставка и платёж; альтернативный продукт — не больше одного
        found.sort(
            key=lambda s: (
                -OUTCOME_RANK[DecisionOutcome(s["result"]["outcome"])],
                s["result"]["rate"],
                s["result"]["monthly_payment"],
            )
        )
        result, product_shown = [], False
        for s in found:
            if s["code"].startswith("product:"):
                if product_shown:
                    continue
                product_shown = True
            result.append(s)
        return result[:4]


def describe(product: Product, p: Params) -> str:
    """Условия одной строкой: «Экспресс-кредит, 710 000 ₽ на 12 мес., с поручительством собственника»."""
    parts = [product.name, f"{fmt_money(p.amount)} на {p.term_months} мес."]
    if pledged(product, p.has_collateral):
        parts.append("с залогом имущества")
    if p.owner_guarantee:
        parts.append("с поручительством собственника")
    return ", ".join(parts)


def _replace(p: Params, **changes) -> Params:
    data = p.to_dict() | changes
    return Params(**data)


def _effect(base: dict, new: dict) -> str | None:
    """Чем вариант лучше текущего; None — если не лучше."""
    rank_base = OUTCOME_RANK[DecisionOutcome(base["outcome"])]
    rank_new = OUTCOME_RANK[DecisionOutcome(new["outcome"])]
    if rank_new < rank_base or new["outcome"] == DecisionOutcome.DECLINE:
        return None  # вариант, который всё равно не одобрят, не подсказываем
    if rank_new > rank_base:
        return f"{new['verdict']} вместо «{base['verdict'].lower()}»"
    parts = []
    if new["rate"] < base["rate"] - 0.05:
        parts.append(f"ставка {_pct(new['rate'])} вместо {_pct(base['rate'])}")
    diff = base["monthly_payment"] - new["monthly_payment"]
    if diff >= max(100.0, base["monthly_payment"] * 0.01):
        parts.append(f"платёж меньше на {_rub(diff)} в месяц")
    if not parts:
        return None
    text = ", ".join(parts)
    overpay = new["total_interest"] - base["total_interest"]
    if overpay >= 1:
        # честно: длинный срок снижает платёж, но увеличивает переплату
        text += f", но переплата больше на {_rub(overpay)}"
    return text[0].upper() + text[1:]
