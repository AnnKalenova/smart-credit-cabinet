from dataclasses import replace
from datetime import date

import pytest

from app.domain.enums import DecisionOutcome
from app.domain.policy import (
    DTI_CAP,
    GRADE_LIMIT_FACTOR,
    PolicyInput,
    ProductParams,
    evaluate,
    indicative_rate,
    max_limit,
    price,
    rate_bounds,
    round_rate,
)
from app.domain.products import PRODUCTS_SEED
from app.domain.scoring import GRADE_PD, Factor, ScoringOutcome

KEY_RATE = 14.0
PRODUCTS = {
    p["code"]: ProductParams(
        code=p["code"],
        name=p["name"],
        purposes=[str(x) for x in p["purposes"]],
        min_amount=float(p["min_amount"]),
        max_amount=float(p["max_amount"]),
        min_term=p["min_term"],
        max_term=p["max_term"],
        base_spread=float(p["base_spread"]),
        max_spread=float(p["max_spread"]),
        turnover_multiplier=float(p["turnover_multiplier"]),
        auto_decision_limit=float(p["auto_decision_limit"]),
        repayment_type=p["repayment_type"],
        collateral=p["collateral"],
    )
    for p in PRODUCTS_SEED
}
SCORES = {"A": 760, "B": 670, "C": 620, "D": 570, "E": 500}


def _scoring(grade: str) -> ScoringOutcome:
    factors = [
        Factor(
            "turnover_stability",
            "Стабильность поступлений",
            "5%",
            25,
            "Стабильные поступления",
            "Стабильные поступления",
        ),
        Factor("credit_history", "Кредитная история", "0", 40, "Без просрочек", "Без просрочек"),
    ]
    return ScoringOutcome(score=SCORES[grade], grade=grade, pd=GRADE_PD[grade], factors=factors, metrics={})


def _input(product="WORKING_CAPITAL", grade="A", turnover=4_600_000.0, **overrides) -> PolicyInput:
    base = {
        "product": PRODUCTS[product],
        "alt_products": list(PRODUCTS.values()),
        "company_kind": "LEGAL",
        "amount": 3_000_000.0,
        "term_months": 12,
        "purpose": "WORKING_CAPITAL",
        "has_collateral": False,
        "owner_guarantee": False,
        "declared_revenue": None,
        "revenue_confirmed": False,
        "revenue_info_rounds": 0,
        "key_rate": KEY_RATE,
        "as_of": date(2026, 10, 23),
        "avg_monthly_turnover": turnover,
        "effective_monthly_turnover": turnover,
        "existing_monthly_payments": 0.0,
        "bank_tenure_months": 31,
        "business_age_months": 60,
        "scoring": _scoring(grade),
        "egrul": {"status": "ACTIVE", "director": {"since": "2018-01-01"}},
        "fns": {"tax_arrears": 0},
        "bki": {"max_overdue_days_12m": 0, "current_overdue_days": 0},
    }
    base.update(overrides)
    return PolicyInput(**base)


def _codes(result) -> set[str]:
    return {r.code for r in result.reasons}


# --------------------------------------------------------------------------- стоп-факторы


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"egrul": {"status": "LIQUIDATING", "status_text": "Находится в стадии ликвидации"}}, "company_status"),
        ({"bki": {"max_overdue_days_12m": 120, "current_overdue_days": 0}}, "bki_overdue_90"),
        ({"bki": {"max_overdue_days_12m": 0, "current_overdue_days": 95}}, "bki_overdue_90"),
        ({"business_age_months": 2}, "business_too_young"),
        ({"scoring": _scoring("E")}, "grade_e"),
    ],
)
def test_stop_factors_decline(overrides, code):
    result = evaluate(_input(**overrides))
    assert result.outcome == DecisionOutcome.DECLINE
    assert code in _codes(result)
    assert result.offers == []
    assert all(r.client_text for r in result.reasons)


@pytest.mark.parametrize("egrul", [{}, {"status": ""}, {"status": None, "director": {"since": "2018-01-01"}}])
def test_missing_registry_status_is_not_treated_as_active(egrul):
    # без статуса в выписке нельзя считать, что бизнес действует: решение принимает специалист
    result = evaluate(_input(egrul=egrul))
    assert result.outcome == DecisionOutcome.MANUAL_REVIEW
    reason = next(r for r in result.reasons if r.code == "registry_status_unknown")
    assert reason.kind == "MANUAL" and reason.text.startswith("В выписке ЕГРЮЛ нет статуса компании")


# --------------------------------------------------------------------------- запрос информации


def test_revenue_mismatch_requests_statement_and_explanation():
    result = evaluate(_input(declared_revenue=4_600_000 * 12 * 2))
    assert result.outcome == DecisionOutcome.NEEDS_INFO
    types = {i["type"] for i in result.info_items}
    assert types == {"DOCUMENT", "COMMENT"}
    assert result.info_items[0]["doc_type"] == "BANK_STATEMENT"


def test_confirmed_revenue_does_not_request_info():
    result = evaluate(_input(declared_revenue=4_600_000 * 12 * 2, revenue_confirmed=True))
    assert result.outcome == DecisionOutcome.APPROVE


def test_unresolved_mismatch_after_two_rounds_goes_to_analyst():
    result = evaluate(_input(declared_revenue=4_600_000 * 12 * 2, revenue_info_rounds=2))
    assert result.outcome == DecisionOutcome.MANUAL_REVIEW
    assert "info_unresolved" in _codes(result)
    assert result.recommendation == DecisionOutcome.APPROVE


def test_mismatch_is_requested_once_more_after_single_revenue_round():
    # один запрос выписки уже был (ответ без выписки не принимается, но на всякий случай): просим второй раз
    result = evaluate(_input(declared_revenue=4_600_000 * 12 * 2, revenue_info_rounds=1))
    assert result.outcome == DecisionOutcome.NEEDS_INFO


# --------------------------------------------------------------------------- одобрение и встречные предложения


def test_approve_within_limit_with_transparent_pricing():
    result = evaluate(_input())
    assert result.outcome == DecisionOutcome.APPROVE
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert not offer.is_counter and offer.amount == 3_000_000 and offer.term_months == 12
    assert offer.rate == 18.5  # 14 + 5 (продукт) + 0 (рейтинг A) − 0,5 (клиент > 2 лет)
    assert sum(p["value"] for p in offer.pricing["parts"]) == pytest.approx(18.5)
    calc = result.details["limit_calculation"]
    assert calc["max_limit"] >= 3_000_000
    assert calc["by_turnover"] == pytest.approx(4_600_000 * 3)
    assert any(r.kind == "POSITIVE" for r in result.reasons)


def test_counter_offer_longer_term_and_smaller_amount():
    # Высокая текущая нагрузка: 30% поступлений уже уходит на платежи по кредитам
    result = evaluate(
        _input(
            product="EXPRESS",
            grade="C",
            turnover=950_000,
            existing_monthly_payments=285_000,
            amount=1_000_000,
            term_months=12,
            purpose="EXPANSION",
        )
    )
    assert result.outcome == DecisionOutcome.COUNTER_OFFER
    longer, smaller = result.offers
    assert longer.is_counter and longer.amount == 1_000_000 and longer.term_months > 12
    assert smaller.is_counter and smaller.amount < 1_000_000 and smaller.term_months == 12
    assert smaller.amount % 10_000 == 0
    # платежи по новому кредиту укладываются в лимит нагрузки
    for o in result.offers:
        assert (285_000 + o.monthly_payment) / 950_000 <= DTI_CAP + 1e-9
    assert "limit_exceeded" in _codes(result)


def test_counter_offer_with_alternative_product():
    # Овердрафт не проходит по минимальной сумме, но под ту же цель подходит оборотный кредит
    result = evaluate(
        _input(
            product="OVERDRAFT", grade="C", turnover=245_000, amount=600_000, term_months=12, purpose="WORKING_CAPITAL"
        )
    )
    assert result.outcome == DecisionOutcome.COUNTER_OFFER
    assert len(result.offers) == 1
    alt = result.offers[0]
    assert alt.product_code == "WORKING_CAPITAL"
    assert alt.label.startswith("Другой продукт")
    assert PRODUCTS["WORKING_CAPITAL"].min_amount <= alt.amount <= 600_000


def test_decline_when_no_capacity_for_any_product():
    result = evaluate(_input(product="EXPRESS", grade="C", turnover=100_000, amount=1_000_000, term_months=24))
    assert result.outcome == DecisionOutcome.DECLINE
    assert "no_capacity" in _codes(result)


# --------------------------------------------------------------------------- ручное рассмотрение


def test_amount_above_auto_limit_goes_to_analyst_with_recommendation():
    result = evaluate(
        _input(
            product="INVESTMENT",
            turnover=20_000_000,
            amount=25_000_000,
            term_months=60,
            purpose="EQUIPMENT",
            has_collateral=True,
        )
    )
    assert result.outcome == DecisionOutcome.MANUAL_REVIEW
    assert result.recommendation == DecisionOutcome.APPROVE
    assert "above_auto_limit" in _codes(result)
    assert result.offers and result.offers[0].amount == 25_000_000
    assert "Залог приобретаемого имущества" in result.offers[0].conditions


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"scoring": _scoring("D")}, "grade_d"),
        ({"bki": {"max_overdue_days_12m": 45, "current_overdue_days": 0}}, "bki_overdue_31_90"),
        ({"fns": {"tax_arrears": 0, "suspended_accounts": True}}, "suspended_accounts"),
        ({"fns": {"tax_arrears": 0, "unreliable_data": True}}, "unreliable_data"),
        ({"fns": {"tax_arrears": 0, "mass_address": True}}, "mass_address"),
        ({"fns": {"tax_arrears": 450_000}}, "tax_arrears"),
        ({"egrul": {"status": "ACTIVE", "director": {"since": "2026-09-01"}}}, "director_changed"),
    ],
)
def test_manual_review_triggers(overrides, code):
    result = evaluate(_input(**overrides))
    assert result.outcome == DecisionOutcome.MANUAL_REVIEW
    assert code in _codes(result)
    assert result.recommendation in (DecisionOutcome.APPROVE, DecisionOutcome.COUNTER_OFFER)


def test_large_working_capital_requires_owner_guarantee_condition():
    result = evaluate(_input(turnover=10_000_000, amount=12_000_000, term_months=18))
    conditions = result.offers[0].conditions
    assert "Поручительство собственника бизнеса" in conditions


# --------------------------------------------------------------------------- ставка и лимит


def test_price_breakdown_and_discounts():
    rate, parts = price(PRODUCTS["EXPRESS"], "B", KEY_RATE, False, False, 0)
    assert rate == 22.0 and len(parts) == 3
    rate, parts = price(PRODUCTS["WORKING_CAPITAL"], "B", KEY_RATE, True, True, 30)
    assert rate == 14 + 5 + 1 - 1 - 0.5 - 0.5
    labels = [p["label"] for p in parts]
    assert any("залог" in label for label in labels) and any("поручительство" in label for label in labels)


def test_price_is_clamped_to_product_range():
    narrow = replace(PRODUCTS["EXPRESS"], max_spread=5.0)
    rate, parts = price(narrow, "C", KEY_RATE, False, False, 0)
    assert rate == KEY_RATE + 5.0
    assert parts[-1]["label"].startswith("Ограничение")
    cheap = replace(PRODUCTS["EXPRESS"], base_spread=-3.0)
    rate, _ = price(cheap, "A", KEY_RATE, False, False, 0)
    assert rate == KEY_RATE + 1.0


def test_max_limit_uses_minimum_of_constraints_and_grade_factor():
    product = PRODUCTS["WORKING_CAPITAL"]
    limit_a, calc = max_limit(product, "A", 18.5, 12, 1_000_000, 0)
    limit_d, _ = max_limit(product, "D", 18.5, 12, 1_000_000, 0)
    assert calc["by_turnover"] == 3_000_000
    assert limit_a == 3_000_000
    assert limit_d == 1_800_000 == 3_000_000 * GRADE_LIMIT_FACTOR["D"]
    none_left, calc = max_limit(product, "A", 18.5, 12, 1_000_000, 500_000)
    assert calc["payment_capacity"] < 0 and none_left == 0


def test_revolving_limit_is_turnover_based():
    limit, calc = max_limit(PRODUCTS["OVERDRAFT"], "A", 20.0, 12, 1_000_000, 0)
    assert calc["by_turnover"] == 500_000
    assert limit == 500_000


def test_indicative_rate_takes_required_collateral_into_account():
    assert indicative_rate(PRODUCTS["EXPRESS"], KEY_RATE) == 22.0
    assert indicative_rate(PRODUCTS["INVESTMENT"], KEY_RATE) == 18.0


# --------------------------------------------------------------------------- тексты причин и условия предложения


def test_registry_status_is_explained_in_plain_words():
    legal = evaluate(_input(egrul={"status": "LIQUIDATING", "status_text": "Находится в стадии ликвидации"}))
    reason = next(r for r in legal.reasons if r.code == "company_status")
    assert reason.client_text == "По данным ЕГРЮЛ компания находится в стадии ликвидации"

    ip = evaluate(_input(company_kind="IP", egrul={"status": "CEASED", "status_text": "Прекратил деятельность"}))
    reason = next(r for r in ip.reasons if r.code == "company_status")
    assert reason.client_text == "По данным ЕГРИП предприниматель прекратил деятельность"

    unknown = evaluate(_input(egrul={"status": "SPECIAL", "status_text": "Особый статус"}))
    reason = next(r for r in unknown.reasons if r.code == "company_status")
    assert "«Особый статус»" in reason.client_text and "действующему бизнесу" in reason.client_text


def test_director_change_date_is_formatted_for_people():
    result = evaluate(_input(egrul={"status": "ACTIVE", "director": {"since": "2026-08-15"}}))
    reason = next(r for r in result.reasons if r.code == "director_changed")
    assert reason.text == "Смена руководителя 15.08.2026"


def test_declared_collateral_becomes_offer_condition_with_discount():
    with_collateral = evaluate(_input(has_collateral=True))
    offer = with_collateral.offers[0]
    assert "Залог имущества, указанного в заявке" in offer.conditions
    assert any(p["label"] == "Скидка за залог" for p in offer.pricing["parts"])

    without = evaluate(_input())
    assert not any("Залог" in c for c in without.offers[0].conditions)

    investment = evaluate(_input(product="INVESTMENT", purpose="EQUIPMENT", amount=2_000_000, term_months=24))
    assert "Залог приобретаемого имущества" in investment.offers[0].conditions


def test_collateral_counts_only_where_product_allows_it():
    from app.domain.products import pledged

    assert pledged(PRODUCTS["INVESTMENT"], False) is True
    assert pledged(PRODUCTS["WORKING_CAPITAL"], True) is True
    assert pledged(PRODUCTS["WORKING_CAPITAL"], False) is False
    assert pledged(PRODUCTS["EXPRESS"], True) is False  # беззалоговый продукт
    express = evaluate(_input(product="EXPRESS", purpose="OTHER", amount=500_000, term_months=6, has_collateral=True))
    offer = express.offers[0]
    assert not any("Залог" in c for c in offer.conditions)
    assert all(p["label"] != "Скидка за залог" for p in offer.pricing["parts"])


def test_alternative_product_is_offered_only_if_nothing_extra_is_needed():
    # экспресс-кредит на оборудование при низких оборотах: инвестиционный кредит требует залога и счёта
    # поставщика, которых в заявке нет, — его нельзя предлагать автоматически
    result = evaluate(
        _input(product="EXPRESS", purpose="EQUIPMENT", amount=1_000_000, term_months=24, turnover=180_000)
    )
    assert all(o.product_code != "INVESTMENT" for o in result.offers)


def test_rate_rounds_half_up_and_breakdown_adds_up_with_fractional_key_rate():
    rate, parts = price(PRODUCTS["EXPRESS"], "B", 16.25, False, False, 0)
    assert rate == 24.3  # 16,25 + 7 + 1 = 24,25 → 24,3
    assert sum(p["value"] for p in parts) == pytest.approx(rate)
    assert any(p["label"] == "Округление до 0,1 п.п." for p in parts)
    assert rate_bounds(PRODUCTS["EXPRESS"], 16.25) == (17.3, 29.2)  # коридор округлён внутрь
    assert round_rate(20.65) == 20.7 and round_rate(20.64) == 20.6


def test_confirmed_revenue_counts_at_most_twice_the_account_turnover():
    from types import SimpleNamespace

    from app.domain.scoring import TurnoverStats
    from app.services.assessment import effective_turnover

    stats = TurnoverStats(months=12, avg_monthly=650_000, cv=0.1, trend=0.0, series=[])
    statement = [SimpleNamespace(doc_type="BANK_STATEMENT")]
    modest = SimpleNamespace(documents=statement, declared_revenue=14_400_000)
    inflated = SimpleNamespace(documents=statement, declared_revenue=144_000_000)
    assert effective_turnover(stats, modest) == pytest.approx(14_400_000 / 12 * 0.8)
    assert effective_turnover(stats, inflated) == pytest.approx(650_000 * 2)


def test_statement_far_above_turnover_goes_to_specialist():
    inflated = evaluate(
        _input(declared_revenue=4_600_000 * 12 * 5, revenue_confirmed=True, effective_monthly_turnover=9_200_000)
    )
    assert inflated.outcome == DecisionOutcome.MANUAL_REVIEW
    assert "revenue_unverified" in _codes(inflated)
    modest = evaluate(_input(declared_revenue=4_600_000 * 12 * 1.5, revenue_confirmed=True))
    assert "revenue_unverified" not in _codes(modest)
