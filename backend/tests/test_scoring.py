from datetime import date

import pytest

from app.domain.scoring import (
    BASE_SCORE,
    GRADE_PD,
    ScoringInput,
    TurnoverStats,
    grade_for,
    months_between,
    score,
    turnover_stats,
)

AS_OF = date(2026, 10, 23)


def _stats(avg: float, cv: float = 0.05, trend: float = 0.0) -> TurnoverStats:
    return TurnoverStats(months=12, avg_monthly=avg, cv=cv, trend=trend, series=[])


def _input(**overrides) -> ScoringInput:
    base = {
        "as_of": AS_OF,
        "company_kind": "LEGAL",
        "registration_date": date(2018, 3, 14),
        "client_since": date(2019, 2, 1),
        "okved_code": "10.71",
        "turnover": _stats(4_600_000),
        "effective_monthly_turnover": 4_600_000,
        "new_monthly_payment": 270_000,
        "egrul": {"status": "ACTIVE", "director": {"since": "2018-03-14"}},
        "fns": {"tax_arrears": 0, "revenue_last_year": 52_400_000, "net_profit_last_year": 5_700_000},
        "bki": {
            "total_monthly_payment": 78_000,
            "max_overdue_days_12m": 0,
            "history_length_months": 64,
            "inquiries_30d": 1,
        },
        "has_collateral": False,
        "owner_guarantee": False,
    }
    base.update(overrides)
    return ScoringInput(**base)


def _points(outcome) -> dict[str, int]:
    return {f.code: f.points for f in outcome.factors}


def test_turnover_stats_constant_and_growing_series():
    flat = turnover_stats([(date(2026, m, 1), 100.0, 90.0) for m in range(1, 13)])
    assert flat.avg_monthly == 100 and flat.cv == 0 and flat.trend == 0
    assert flat.series[0] == {"month": "2026-01", "credit": 100.0, "debit": 90.0}
    growing = turnover_stats([(date(2026, m, 1), 100.0 + m * 10, 0.0) for m in range(1, 13)])
    assert growing.trend > 0.10


def test_turnover_stats_empty_and_short_series():
    empty = turnover_stats([])
    assert empty.months == 0 and empty.cv == 1.0
    short = turnover_stats([(date(2026, m, 1), 100.0 * m, 0.0) for m in range(1, 5)])
    assert short.trend == 0.0  # меньше 6 месяцев — тренд не считается


def test_months_between():
    assert months_between(date(2020, 1, 15), date(2020, 2, 14)) == 0
    assert months_between(date(2020, 1, 15), date(2020, 2, 15)) == 1
    assert months_between(date(2021, 5, 1), date(2020, 1, 1)) == 0


@pytest.mark.parametrize(
    "value,grade",
    [(900, "A"), (700, "A"), (699, "B"), (650, "B"), (649, "C"), (600, "C"), (599, "D"), (550, "D"), (549, "E")],
)
def test_grade_thresholds(value, grade):
    assert grade_for(value) == grade


def test_strong_company_gets_grade_a_and_transparent_factors():
    out = score(_input())
    pts = _points(out)
    assert pts["business_age"] == 35
    assert pts["bank_tenure"] == 15
    assert pts["turnover_stability"] == 25
    assert pts["turnover_trend"] == 0
    assert pts["debt_load"] == 40  # (78k + 270k) / 4,6 млн ≈ 7,6%
    assert pts["credit_history"] == 40
    assert pts["tax_arrears"] == 10
    assert pts["industry"] == 10
    assert pts["profitability"] == 20  # 5,7 / 52,4 ≈ 10,9%
    assert pts["registry_flags"] == 0
    assert out.score == BASE_SCORE + sum(pts.values())
    assert out.grade == "A"
    assert out.pd == GRADE_PD["A"]
    assert out.metrics["dti"] == pytest.approx((78_000 + 270_000) / 4_600_000, abs=1e-4)
    assert all(f.comment for f in out.factors)


def test_weak_company_hits_every_negative_factor_and_is_clipped():
    out = score(
        _input(
            registration_date=date(2026, 1, 10),
            client_since=date(2026, 6, 1),
            okved_code="41.20",
            turnover=_stats(300_000, cv=0.5, trend=-0.3),
            effective_monthly_turnover=300_000,
            new_monthly_payment=100_000,
            egrul={"status": "ACTIVE", "director": {"since": "2026-09-01"}},
            fns={
                "tax_arrears": 1_500_000,
                "revenue_last_year": 10_000_000,
                "net_profit_last_year": -500_000,
                "suspended_accounts": True,
                "unreliable_data": True,
                "mass_address": True,
            },
            bki={
                "total_monthly_payment": 50_000,
                "max_overdue_days_12m": 120,
                "history_length_months": 30,
                "inquiries_30d": 6,
            },
        )
    )
    pts = _points(out)
    assert pts["business_age"] == -45
    assert pts["bank_tenure"] == -15
    assert pts["turnover_stability"] == -20
    assert pts["turnover_trend"] == -35
    assert pts["debt_load"] == -70
    assert pts["credit_history"] == -150
    assert pts["credit_inquiries"] == -30
    assert pts["tax_arrears"] == -60
    assert pts["industry"] == -15
    assert pts["profitability"] == -35
    assert pts["registry_flags"] == -50 - 40 - 25 - 15
    assert out.score == 300  # нижняя граница шкалы
    assert out.grade == "E"
    assert len(out.metrics["registry_flags"]) == 4


def test_no_turnover_means_maximal_debt_load_penalty():
    out = score(_input(turnover=_stats(0), effective_monthly_turnover=0))
    assert _points(out)["debt_load"] == -70
    assert out.metrics["dti"] == 9.99


def test_ip_without_statements_is_scored_by_turnover_only():
    out = score(
        _input(company_kind="IP", fns={"tax_arrears": 0, "revenue_last_year": 10_900_000, "net_profit_last_year": None})
    )
    factor = next(f for f in out.factors if f.code == "profitability")
    assert factor.points == 0 and factor.value == "—"


def test_thin_credit_file_and_security_bonus():
    out = score(
        _input(
            bki={"total_monthly_payment": 0, "max_overdue_days_12m": 0, "history_length_months": 0, "inquiries_30d": 0},
            has_collateral=True,
            owner_guarantee=True,
        )
    )
    pts = _points(out)
    assert pts["credit_history"] == -5
    assert pts["security"] == 30


@pytest.mark.parametrize(
    "overdue,history,expected",
    [(0, 6, 10), (5, 40, -25), (45, 40, -80), (91, 40, -150)],
)
def test_credit_history_buckets(overdue, history, expected):
    bki = {
        "total_monthly_payment": 0,
        "max_overdue_days_12m": overdue,
        "history_length_months": history,
        "inquiries_30d": 0,
    }
    assert _points(score(_input(bki=bki)))["credit_history"] == expected
