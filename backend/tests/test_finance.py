import importlib.util
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.finance import (
    add_months,
    annuity_payment,
    annuity_principal,
    build_annuity_schedule,
    build_revolving_schedule,
    floor_to,
    inn_is_valid,
    money,
    offer_economics,
)

REPO = Path(__file__).resolve().parents[2]


def _generator():
    spec = importlib.util.spec_from_file_location("synthetic_generator", REPO / "tools" / "generate_synthetic_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_annuity_payment_matches_reference_value():
    # 1,2 млн под 12% на 12 месяцев: ставка 1% в месяц
    assert annuity_payment(1_200_000, 12.0, 12) == pytest.approx(106_618.55, abs=0.01)


def test_annuity_payment_zero_rate_is_linear():
    assert annuity_payment(1_200, 0, 12) == 100


def test_annuity_payment_rejects_non_positive_term():
    with pytest.raises(ValueError):
        annuity_payment(1_000, 10, 0)


@pytest.mark.parametrize("amount,rate,months", [(1_000_000, 18.5, 12), (25_000_000, 16.5, 60), (750_000, 23.0, 7)])
def test_annuity_principal_is_inverse_of_payment(amount, rate, months):
    assert annuity_principal(annuity_payment(amount, rate, months), rate, months) == pytest.approx(amount, rel=1e-9)


def test_annuity_principal_non_positive_payment_gives_zero():
    assert annuity_principal(0, 20, 12) == 0
    assert annuity_principal(-5, 20, 12) == 0


def test_annuity_schedule_repays_principal_exactly():
    rows = build_annuity_schedule(3_000_000, 18.5, 12, date(2026, 10, 23))
    assert len(rows) == 12
    assert sum(r["principal"] for r in rows) == pytest.approx(3_000_000, abs=0.01)
    assert rows[-1]["balance"] == 0
    assert rows[0]["date"] == "2026-11-23"
    payments = [r["payment"] for r in rows]
    assert max(payments[:-1]) - min(payments[:-1]) < 0.02  # равные платежи
    assert abs(payments[-1] - payments[0]) < 5  # последний платёж выравнивает копейки
    interests = [r["interest"] for r in rows]
    assert interests == sorted(interests, reverse=True)  # проценты убывают


def test_revolving_schedule_is_interest_only_until_maturity():
    rows = build_revolving_schedule(360_000, 21.0, 12, date(2026, 1, 31))
    assert len(rows) == 12
    assert all(r["principal"] == 0 for r in rows[:-1])
    assert rows[-1]["principal"] == 360_000
    assert rows[-1]["balance"] == 0
    assert rows[0]["interest"] == pytest.approx(360_000 * 0.21 / 12, abs=0.01)
    assert rows[0]["date"] == "2026-02-28"


def test_offer_economics_annuity_and_revolving():
    monthly, interest = offer_economics(1_000_000, 22.0, 24, "ANNUITY")
    rows = build_annuity_schedule(1_000_000, 22.0, 24, date(2026, 1, 15))
    # переплата в предложении совпадает с суммой процентов в графике до копейки
    assert interest == pytest.approx(sum(r["interest"] for r in rows), abs=0.001)
    assert monthly == rows[0]["payment"]
    assert abs(interest - (monthly * 24 - 1_000_000)) < 2  # последний платёж выравнивает остаток
    monthly_od, interest_od = offer_economics(500_000, 24.0, 12, "REVOLVING")
    assert monthly_od == 10_000
    assert interest_od == 120_000


def test_add_months_clamps_day_and_rolls_year():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert add_months(date(2026, 3, 15), -3) == date(2025, 12, 15)


def test_floor_to_and_money():
    assert floor_to(1_234_567, 10_000) == 1_230_000
    assert floor_to(-5, 10) == 0
    assert money(1.005) == Decimal("1.01")
    assert money(Decimal("2.344")) == Decimal("2.34")


def test_inn_validation_known_and_broken_values():
    assert inn_is_valid("7707083893")  # контрольная сумма корректна
    assert not inn_is_valid("7707083894")  # изменена контрольная цифра
    assert not inn_is_valid("12345")
    assert not inn_is_valid("77070838AB")


def test_synthetic_inns_are_valid_and_cannot_be_real():
    gen = _generator()
    data = json.loads((REPO / "data" / "synthetic" / "companies.json").read_text(encoding="utf-8"))
    for company in data["companies"]:
        assert inn_is_valid(company["inn"]), company["inn"]
        assert company["inn"].startswith("0000")  # код региона и инспекции 0000 не существует
    for serial in (1, 12345, 99999):
        assert inn_is_valid(gen.inn_legal(serial))
    for serial in (1, 220512, 999999):
        assert inn_is_valid(gen.inn_person(serial))


def test_synthetic_ogrn_check_digits():
    gen = _generator()
    ogrn = gen.ogrn(2018, 10471)
    assert len(ogrn) == 13 and int(ogrn[:12]) % 11 % 10 == int(ogrn[12])
    ogrnip = gen.ogrnip(2024, 220512)
    assert len(ogrnip) == 15 and int(ogrnip[:14]) % 13 % 10 == int(ogrnip[14])


def test_rounding_is_half_up_to_kopecks():
    from app.domain.finance import r2

    assert r2(0.125) == 0.13 and r2(2.675) == 2.68 and r2(-0.125) == -0.13
