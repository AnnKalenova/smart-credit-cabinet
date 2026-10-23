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
    offered = {(o["amount"], o["term_months"]) for o in app["offers"]}
    term = by_code["term"]
    assert term["result"]["outcome"] == "APPROVE"
    assert term["effect"].startswith("Одобрим автоматически вместо")
    # варианты банка (вся сумма на 18 мес., 710 000 ₽ на 12 мес.) не повторяются в подсказках
    assert (term["params"]["amount"], term["params"]["term_months"]) not in offered
    assert "amount" not in by_code
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


def test_offer_conditions_are_chosen_not_reapplied(login):
    c = login("kuznetsova")
    app = c.prepare_and_submit("EXPRESS", "EXPANSION", 1_000_000, 12)
    offers = {(o["amount"], o["term_months"]): o for o in app["offers"]}
    data = _simulate(c, app)
    # подсказки не повторяют предложение банка: его выбирают в предложении, без новой заявки
    for s in data["suggestions"]:
        assert (s["params"]["amount"], s["params"]["term_months"]) not in offers
    assert data["result"]["offer"] is None
    amount, term = next(iter(offers))
    same = _simulate(c, app, amount=amount, term_months=term, with_suggestions=False)["result"]
    assert same["offer"]["id"] == offers[(amount, term)]["id"]
    # с поручительством — уже другие условия, не предложение банка
    other = _simulate(c, app, amount=amount, term_months=term, owner_guarantee=True, with_suggestions=False)
    assert other["result"]["offer"] is None


def test_other_product_needs_nothing_missing_and_todo_is_honest(login):
    c = login("kuznetsova")
    app = c.prepare_and_submit("EXPRESS", "EXPANSION", 1_000_000, 12)
    data = _simulate(c, app)
    # инвестиционному кредиту нужны залог и счёт поставщика — в подсказки он не попадает
    assert not any(s["code"].startswith("product:") for s in data["suggestions"])
    assert data["result"]["todo"] == []
    investment = _simulate(c, app, product_code="INVESTMENT", amount=1_000_000, term_months=24)["result"]
    assert investment["todo"] == [
        "описать имущество для залога",
        "приложить документ: счёт или спецификация поставщика",
    ]

    s = login("smirnov")
    wc = s.prepare_and_submit("WORKING_CAPITAL", "WORKING_CAPITAL", 3_000_000, 12)
    pledged = _simulate(s, wc, has_collateral=True, with_suggestions=False)["result"]
    assert pledged["todo"] == ["описать имущество для залога"]


def test_replace_current_offer_in_one_step(login):
    c = login("kuznetsova")
    app = c.prepare_and_submit("EXPRESS", "EXPANSION", 1_000_000, 12)
    best = _simulate(c, app)["suggestions"][0]
    # без явного согласия закрыть текущую заявку новая не создаётся
    c.call("POST", _url(app, "/reapply"), json=best["params"], expect=409)
    # ошибка в условиях — текущее предложение остаётся в силе
    c.call("POST", _url(app, "/reapply"), json={"amount": 50_000_000, "replace_current": True}, expect=422)
    assert c.get(app["id"])["status"] == "OFFER_READY"

    new = c.call("POST", _url(app, "/reapply"), json={**best["params"], "replace_current": True})
    assert new["status"] == "DRAFT" and new["parent_id"] == app["id"]
    old = c.get(app["id"])
    assert old["status"] == "CLIENT_DECLINED"
    assert old["next_application"]["number"] == new["number"]
    assert "Отказ от предложения: клиент подаёт заявку на других условиях" in [e["title"] for e in old["events"]]
    # повторная кнопка не создаст вторую заявку
    c.call("POST", _url(app, "/reapply"), json={**best["params"], "replace_current": True}, expect=409)


def test_replace_application_under_manual_review(login):
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
    new = c.call("POST", _url(app, "/reapply"), json={"amount": 15_000_000, "replace_current": True})
    assert new["amount"] == 15_000_000 and new["collateral_description"] == "Обрабатывающий центр с ЧПУ"
    assert c.get(app["id"])["status"] == "CANCELLED"


def test_employee_passes_conditions_to_signer(login):
    s, o = login("smirnov"), login("orlova")
    app = s.prepare_and_submit("EXPRESS", "OTHER", 500_000, 6)
    assert app["status"] == "OFFER_READY"
    proposal = {"amount": 400_000, "term_months": 12, "owner_guarantee": True}
    # без права подписи заменить заявку нельзя — можно передать условия руководителю
    o.call("POST", _url(app, "/reapply"), json={**proposal, "replace_current": True}, expect=403)
    o.call("POST", _url(app, "/request-signature"), json={"conditions": {"amount": 1}}, expect=422)
    o.call("POST", _url(app, "/request-signature"), json={"conditions": proposal})

    view = s.get(app["id"])
    assert view["proposed_conditions"]["conditions"] == {
        "product_code": "EXPRESS",
        "amount": 400_000,
        "term_months": 12,
        "has_collateral": False,
        "owner_guarantee": True,
    }
    assert view["proposed_conditions"]["by"] == "Орлова Мария Петровна"
    text = "Экспресс-кредит, 400 000 ₽ на 12 мес., с поручительством собственника"
    assert view["proposed_conditions"]["text"] == text
    notes = s.call("GET", "/api/v1/notifications")["items"]
    assert notes[0]["title"] == f"Новые условия по заявке № {app['number']}" and text in notes[0]["body"]
    # руководитель подаёт новую заявку на этих условиях — предложение закрывается
    s.call("POST", _url(app, "/reapply"), json={**view["proposed_conditions"]["conditions"], "replace_current": True})
    assert s.get(app["id"])["proposed_conditions"] is None
