"""Конструктор условий: решение банка при других сумме, сроке, продукте, залоге и поручительстве —
той же моделью и политикой, что и решение по заявке, — и новая заявка на выбранных условиях."""

from tests.conftest import drain


def _url(app: dict, suffix: str = "") -> str:
    return f"/api/v1/applications/{app['id']}{suffix}"


def _simulate(user, app: dict, **conditions) -> dict:
    return user.call("POST", _url(app, "/simulation"), json=conditions)


def test_counter_offer_shows_what_gets_approval(login):
    c = login("kuznetsova")
    app = c.prepare_and_submit("EXPRESS", "EXPANSION", 1_000_000, 12)
    assert app["decision"]["outcome"] == "COUNTER_OFFER"

    data = _simulate(c, app)
    assert data["base"] == {
        "product_code": "EXPRESS",
        "amount": 1_000_000,
        "term_months": 12,
        "has_collateral": False,
        "owner_guarantee": False,
    }
    result = data["result"]
    # тот же расчёт, что и решение по заявке
    assert result["outcome"] == "COUNTER_OFFER" and result["verdict"] == "Предложим другие условия"
    assert result["payment_share"] > result["payment_share_cap"] == 0.4
    assert result["fixable"] is True and result["max_amount"] < 1_000_000
    assert [o["amount"] for o in result["offers"]] == [o["amount"] for o in app["offers"]]

    by_code = {s["code"]: s for s in data["suggestions"]}
    assert by_code["term"]["result"]["outcome"] == "APPROVE"
    assert by_code["term"]["effect"].startswith("Одобрим автоматически вместо")
    assert by_code["amount"]["params"]["amount"] <= result["max_amount"]
    assert by_code["amount"]["result"]["outcome"] == "APPROVE"
    assert len(data["suggestions"]) <= 4
    # границы для элементов управления — только продукты под цель заявки
    assert {p["code"] for p in data["products"]} == {"EXPRESS", "INVESTMENT"}

    # клиент сам подбирает условия: другой срок — сразу другое решение
    longer = _simulate(c, app, term_months=by_code["term"]["params"]["term_months"], with_suggestions=False)
    assert longer["result"]["outcome"] == "APPROVE" and longer["suggestions"] == []


def test_new_application_on_chosen_conditions(login):
    c = login("kuznetsova")
    app = c.prepare_and_submit("EXPRESS", "EXPANSION", 1_000_000, 12)
    best = _simulate(c, app)["suggestions"][0]
    c.call("POST", _url(app, "/offers/decline"))
    new = c.call("POST", _url(app, "/reapply"), json=best["params"])
    assert new["status"] == "DRAFT" and new["parent_id"] == app["id"]
    assert new["amount"] == best["params"]["amount"] and new["term_months"] == best["params"]["term_months"]
    assert new["owner_guarantee"] == best["params"]["owner_guarantee"]
    c.sign_consents(new["id"])
    c.submit(new["id"])
    drain()
    # решение по новой заявке совпадает с тем, что показал конструктор
    assert c.get(new["id"])["decision"]["outcome"] == best["result"]["outcome"]


def test_stop_factor_cannot_be_fixed_by_conditions(login):
    f = login("fedorov")
    app = f.prepare_and_submit("EXPRESS", "WORKING_CAPITAL", 2_000_000, 24)
    assert app["status"] == "DECLINED"
    data = _simulate(f, app, amount=500_000, term_months=36)
    assert data["result"]["outcome"] == "DECLINE" and data["result"]["fixable"] is False
    assert "ликвидации" in data["result"]["explanation"]
    assert data["suggestions"] == []  # то, что всё равно не одобрят, не подсказываем


def test_amount_above_auto_limit_suggests_automatic_decision(login):
    c = login("lebedev")
    app = c.prepare_and_submit(
        "INVESTMENT",
        "EQUIPMENT",
        25_000_000,
        60,
        docs=["SUPPLIER_INVOICE", "FIN_STATEMENTS"],
        collateral_description="Обрабатывающий центр с ЧПУ",
    )
    assert app["status"] == "MANUAL_REVIEW"
    data = _simulate(c, app)
    assert data["result"]["outcome"] == "MANUAL_REVIEW" and data["result"]["automatic"] is False
    assert "лимита автоматического решения" in data["result"]["explanation"]
    amount = next(s for s in data["suggestions"] if s["code"] == "amount")
    assert amount["result"]["outcome"] == "APPROVE"
    assert amount["params"]["amount"] <= 15_000_000


def test_collateral_and_guarantee_lower_the_rate(login):
    c = login("smirnov")
    app = c.prepare_and_submit("WORKING_CAPITAL", "WORKING_CAPITAL", 3_000_000, 12)
    assert app["status"] == "OFFER_READY"
    base = _simulate(c, app, with_suggestions=False)["result"]
    pledged = _simulate(c, app, has_collateral=True, with_suggestions=False)["result"]
    both = _simulate(c, app, has_collateral=True, owner_guarantee=True, with_suggestions=False)["result"]
    assert pledged["rate"] == round(base["rate"] - 1.0, 1)
    assert both["rate"] == round(base["rate"] - 1.5, 1)
    assert both["monthly_payment"] < base["monthly_payment"]
    # у беззалогового продукта флаг залога не действует
    express = _simulate(c, app, product_code="EXPRESS", amount=3_000_000, has_collateral=True)["result"]
    assert express["has_collateral"] is False


def test_simulation_validates_conditions(login):
    c = login("smirnov")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 500_000, 6)
    resp = c.call("POST", _url(app, "/simulation"), json={"amount": 50_000_000}, expect=422)
    assert resp["detail"] == "Сумма по продукту «Экспресс-кредит» — от 300 000 до 5 000 000 ₽"
    resp = c.call("POST", _url(app, "/simulation"), json={"term_months": 48}, expect=422)
    assert resp["detail"] == "Срок по продукту «Экспресс-кредит» — 3–36 мес."
    resp = c.call("POST", _url(app, "/simulation"), json={"product_code": "INVESTMENT"}, expect=422)
    assert resp["detail"] == "Продукт «Инвестиционный кредит» не подходит для цели заявки"


def test_simulation_needs_an_assessed_application(login):
    c = login("smirnov")
    draft = c.draft("EXPRESS", "OTHER", 500_000, 6)
    resp = c.call("POST", _url(draft, "/simulation"), json={}, expect=409)
    assert resp["detail"] == "Конструктор условий доступен после того, как банк оценит заявку"
    # чужая заявка и сотрудники банка
    app = c.prepare_and_submit("EXPRESS", "OTHER", 500_000, 6)
    login("kuznetsova").call("POST", _url(app, "/simulation"), json={}, expect=404)
    login("analyst").call("POST", _url(app, "/simulation"), json={}, expect=403)


def test_simulation_changes_nothing(login):
    c = login("smirnov")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 500_000, 6)
    before = c.get(app["id"])
    _simulate(c, app, amount=4_000_000, term_months=36, owner_guarantee=True)
    after = c.get(app["id"])
    assert after["amount"] == before["amount"] and after["offers"] == before["offers"]
    assert after["events"] == before["events"]
