"""Обязательные данные в ответах внешних систем: полный ответ принимается, неполный — считается сбоем."""

import pytest
from starlette.testclient import TestClient

from app.integrations.contracts import contract_problem
from tests.conftest import MOCK

INN = "0000104715"  # ООО «Северный хлеб»


@pytest.fixture(scope="module")
def answers() -> dict[str, dict]:
    """Настоящие ответы симулятора — тех же форматов, что приходят в конвейер."""
    mock = TestClient(MOCK.app)
    loan = {
        "inn": INN,
        "contract_number": "КД-test",
        "product_code": "EXPRESS",
        "amount": 300_000,
        "term_months": 6,
        "rate": 22.5,
        "settlement_account": "40702810000000104715",
        "idempotency_key": "contracts-test",
    }
    return {
        "EGRUL": mock.get(f"/egrul/v1/entities/{INN}").json(),
        "FNS": mock.get(f"/fns/v1/transparent-business/{INN}").json(),
        "BKI": mock.post("/bki/v1/credit-reports", json={"inn": INN, "consent_id": "c-1"}).json(),
        "ABS": mock.post("/abs/v1/loans", json=loan).json(),
    }


def test_simulator_answers_are_complete(answers):
    for service in ("EGRUL", "FNS", "BKI"):
        assert contract_problem(service, answers[service], {"inn": INN}) is None, service
    assert contract_problem("ABS", answers["ABS"], {"amount": 300_000}) is None


@pytest.mark.parametrize("service", ["EGRUL", "FNS", "BKI", "ABS"])
def test_empty_answer_is_a_failure(service):
    assert contract_problem(service, {}) == "пустой ответ: нет ни одного обязательного поля"
    assert contract_problem(service, {"source": "x"}).startswith("неполный ответ: нет полей ")


def test_missing_fields_are_named(answers):
    egrul = {k: v for k, v in answers["EGRUL"].items() if k not in ("status", "director")}
    assert contract_problem("EGRUL", egrul) == "неполный ответ: нет полей status, director"
    fns = {**answers["FNS"], "suspended_accounts": None, "tax_arrears": -5}
    assert contract_problem("FNS", fns) == "неполный ответ: некорректные значения: tax_arrears, suspended_accounts"


def test_director_appointment_date_is_required(answers):
    # без даты назначения не проверить недавнюю смену руководителя — такой ответ не считается полным
    director = {k: v for k, v in answers["EGRUL"]["director"].items() if k != "since"}
    assert (
        contract_problem("EGRUL", {**answers["EGRUL"], "director": director})
        == "неполный ответ: нет полей director.since"
    )
    nulled = {**answers["EGRUL"], "director": {**director, "since": None}}
    assert contract_problem("EGRUL", nulled) == "неполный ответ: некорректные значения: director.since"


def test_credit_history_must_have_overdue_data_unless_not_found(answers):
    found = {k: v for k, v in answers["BKI"].items() if k not in ("max_overdue_days_12m", "current_overdue_days")}
    assert contract_problem("BKI", found) == ("неполный ответ: нет полей max_overdue_days_12m, current_overdue_days")
    not_found = {"inn": INN, "found": False, "history_length_months": 0, "loans": [], "total_monthly_payment": 0}
    assert contract_problem("BKI", not_found, {"inn": INN}) is None


def test_answer_about_another_company_or_amount_is_rejected(answers):
    assert contract_problem("EGRUL", answers["EGRUL"], {"inn": "7700000000"}) == (
        f"ответ не совпадает с запросом: inn = {INN}, запрошено 7700000000"
    )
    assert contract_problem("ABS", answers["ABS"], {"amount": 500_000}).startswith("ответ не совпадает с запросом")


@pytest.mark.parametrize("data", [None, [], "OK", 42])
def test_not_an_object(data):
    assert contract_problem("EGRUL", data) == "неполный ответ: вместо объекта JSON пришло другое значение"
