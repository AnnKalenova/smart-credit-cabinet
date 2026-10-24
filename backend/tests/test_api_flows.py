"""Сквозные сценарии кредитного конвейера через API: от черновика до выдачи и все альтернативные исходы."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text, update

from tests.conftest import drain

FLOW_STATUSES = [
    "DRAFT",
    "SUBMITTED",
    "PRECHECK",
    "ENRICHMENT",
    "SCORING",
    "DECISION",
    "OFFER_READY",
    "SIGNING",
    "DISBURSEMENT",
    "ISSUED",
]


def _url(app: dict, suffix: str = "") -> str:
    return f"/api/v1/applications/{app['id']}{suffix}"


def _bank(app: dict, suffix: str = "") -> str:
    return f"/api/v1/bank/applications/{app['id']}{suffix}"


# --------------------------------------------------------------------------- справочники и подбор


def test_dictionaries_products_profile_and_match(login):
    u = login("smirnov")
    d = u.call("GET", "/api/v1/dictionaries")
    assert d["key_rate"] == 14.0 and d["demo_mode"] is True
    assert len(d["purposes"]) == 8 and "BANK_STATEMENT" in d["doc_types"]
    products = u.call("GET", "/api/v1/products")
    assert [p["code"] for p in products] == ["EXPRESS", "WORKING_CAPITAL", "INVESTMENT", "OVERDRAFT"]

    profile = u.call("GET", f"/api/v1/companies/{u.company_id}")
    assert profile["turnover"]["months"] == 12
    assert profile["preapproved"]["product_code"] == "EXPRESS" and profile["preapproved"]["amount"] > 0
    assert profile["revenue_estimate"] > 0

    match = u.call(
        "POST",
        "/api/v1/products/match",
        json={"company_id": u.company_id, "purpose": "WORKING_CAPITAL", "amount": 3_000_000, "term_months": 12},
    )
    codes = [r["product_code"] for r in match["results"]]
    assert match["results"][0]["recommended"] is True
    assert "WORKING_CAPITAL" in codes and "INVESTMENT" not in codes
    u.call(
        "POST",
        "/api/v1/products/match",
        json={"company_id": u.company_id, "purpose": "CASINO", "amount": 1, "term_months": 1},
        expect=422,
    )


# --------------------------------------------------------------------------- основной путь


def test_auto_approve_flow_to_issued(login):
    u = login("smirnov")
    app = u.prepare_and_submit("WORKING_CAPITAL", "WORKING_CAPITAL", 3_000_000, 12, purpose_details="Закупка муки")
    assert app["status"] == "OFFER_READY"
    assert app["decision"]["outcome"] == "APPROVE" and app["decision"]["kind"] == "AUTO"
    assert app["decision"]["positives"]
    offer = app["offers"][0]
    assert offer["amount"] == 3_000_000 and offer["rate"] == 18.5 and not offer["is_counter"]
    assert len(offer["schedule_preview"]) == 6

    final = u.accept_and_sign(app)
    assert final["status"] == "ISSUED"
    loan = final["loan"]
    assert loan["loan_account"].startswith("45206") and loan["amount"] == 3_000_000

    loans = u.call("GET", "/api/v1/loans", params={"company_id": u.company_id})
    assert any(item["id"] == loan["id"] for item in loans)
    detail = u.call("GET", f"/api/v1/loans/{loan['id']}")
    assert len(detail["schedule"]) == 12
    assert sum(r["principal"] for r in detail["schedule"]) == pytest.approx(3_000_000, abs=0.05)

    statuses = [e["to_status"] for e in final["events"] if e["to_status"]]
    assert statuses == FLOW_STATUSES
    contract = u.call("GET", _url(app, "/contract"))
    assert contract["contract_number"] == f"КД-{app['number']}" and len(contract["schedule"]) == 12
    notes = u.call("GET", "/api/v1/notifications")
    assert any(n["application_id"] == app["id"] and "кредит выдан" in n["title"] for n in notes["items"])
    u.call("POST", "/api/v1/notifications/read-all", expect=204)
    assert u.call("GET", "/api/v1/notifications")["unread"] == 0


def test_decline_flow_and_reapply(login):
    u = login("fedorov")
    app = u.prepare_and_submit("EXPRESS", "WORKING_CAPITAL", 2_000_000, 24)
    assert app["status"] == "DECLINED"
    assert any("ликвидации" in r for r in app["decision"]["reasons"])
    # выписка показала ликвидацию: «предварительно одобрено» на главной больше не обещается
    assert u.call("GET", f"/api/v1/companies/{u.company_id}")["preapproved"] is None
    assert app["permissions"]["can_reapply"] is True
    new = u.call("POST", _url(app, "/reapply"))
    assert new["status"] == "DRAFT" and new["parent_id"] == app["id"] and new["amount"] == 2_000_000


def test_needs_info_flow_with_bank_statement(login):
    u = login("morozov")
    app = u.prepare_and_submit("OVERDRAFT", "CASH_GAP", 350_000, 12, declared_revenue=14_400_000)
    assert app["status"] == "NEEDS_INFO"
    request = app["info_request"]
    assert request["status"] == "OPEN"
    assert {i["type"] for i in request["items"]} == {"DOCUMENT", "COMMENT"}

    problems = u.call("POST", _url(app, "/info-response"), json={"comment": "Эквайринг в другом банке"}, expect=422)
    assert any("Приложите" in p for p in problems["detail"]["problems"])
    u.demo_doc(app["id"], "BANK_STATEMENT")
    u.call("POST", _url(app, "/info-response"), json={}, expect=422)  # без пояснения
    u.call("POST", _url(app, "/info-response"), json={"comment": "Эквайринг кофейни подключён в другом банке"})
    drain()

    app = u.get(app["id"])
    assert app["status"] == "OFFER_READY"
    assert app["offers"][0]["amount"] == 350_000
    assert app["info_request"]["status"] == "ANSWERED"


def test_counter_offer_messages_and_back_to_choice(login):
    c = login("kuznetsova")
    app = c.prepare_and_submit("EXPRESS", "EXPANSION", 1_000_000, 12)
    assert app["status"] == "OFFER_READY" and app["decision"]["outcome"] == "COUNTER_OFFER"
    assert len(app["offers"]) == 2 and all(o["is_counter"] for o in app["offers"])
    assert app["decision"]["reasons"]  # клиент видит, почему условия изменены

    c.call("POST", _url(app, "/messages"), json={"text": "Можно ли погасить досрочно?"})
    a = login("analyst")
    assert a.call("GET", _bank(app, "/messages"))[-1]["author_role"] == "CLIENT"
    a.call("POST", _bank(app, "/messages"), json={"text": "Да, без комиссии"})
    assert c.call("GET", _url(app, "/messages"))[-1]["author_name"] == "Волкова Ирина Андреевна"
    notes = c.call("GET", "/api/v1/notifications")
    assert any(n["application_id"] == app["id"] and n["title"].startswith("Сообщение") for n in notes["items"])

    c.call("POST", _url(app, f"/offers/{app['offers'][0]['id']}/accept"))
    back = c.call("POST", _url(app, "/signing/back"))
    assert back["status"] == "OFFER_READY" and len(back["offers"]) == 2
    declined = c.call("POST", _url(app, "/offers/decline"))
    assert declined["status"] == "CLIENT_DECLINED"


# --------------------------------------------------------------------------- работа аналитика


def test_manual_review_and_analyst_approval(login):
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
    assert app["status_view"]["title"] == "У кредитного специалиста"
    c.call("POST", _url(app, f"/offers/{uuid.uuid4()}/accept"), expect=409)

    a = login("analyst")
    queue = a.call("GET", "/api/v1/bank/applications", params={"scope": "queue"})
    row = next(r for r in queue if r["id"] == app["id"])
    assert row["grade"] == "A" and row["triggers"]
    dossier = a.call("GET", _bank(app))
    assert set(dossier["sources"]) == {"EGRUL", "FNS", "BKI", "INTERNAL"}
    assert dossier["scoring"]["grade"] == "A" and dossier["permissions"]["can_decide"]
    # подсказки по ставкам — только по продуктам под цель заявки («Купить оборудование»)
    assert {h["product_code"] for h in dossier["pricing_hints"]} == {"EXPRESS", "INVESTMENT"}
    rec = dossier["decisions"][0]["details"]["offers"][0]

    a.call("POST", _bank(app, "/assign"))
    offer = {"product_code": "INVESTMENT", "amount": rec["amount"], "term_months": rec["term_months"]}
    a.call("POST", _bank(app, "/decision"), json={"action": "APPROVE", "offers": [{**offer, "rate": 50}]}, expect=422)
    a.call("POST", _bank(app, "/decision"), json={"action": "APPROVE", "offers": []}, expect=422)
    a.call(
        "POST",
        _bank(app, "/decision"),
        json={"action": "APPROVE", "offers": [offer], "message_to_client": "Одобрено, залог — станок"},
    )

    app = c.get(app["id"])
    assert app["status"] == "OFFER_READY"
    assert app["decision"]["kind"] == "MANUAL" and app["decision"]["message"] == "Одобрено, залог — станок"
    # нагрузка, с которой специалист принимал решение, — для перепроверки при принятии предложения
    assert a.call("GET", _bank(app))["decisions"][0]["details"]["existing_monthly_payments"] >= 0
    assert any(m["author_role"] == "ANALYST" for m in c.call("GET", _url(app, "/messages")))
    assert c.accept_and_sign(app)["status"] == "ISSUED"
    dossier = a.call("GET", _bank(app))
    assert dossier["assignee"] == "Волкова Ирина Андреевна"
    doc = dossier["documents"][0]
    assert a.call("GET", _bank(app, f"/documents/{doc['id']}")).content


def test_analyst_requests_info_then_declines(login):
    c = login("nikitin")
    app = c.prepare_and_submit("WORKING_CAPITAL", "CONTRACT", 8_000_000, 18, docs=["CONTRACT_COPY"])
    assert app["status"] == "MANUAL_REVIEW"
    a = login("analyst")
    dossier = a.call("GET", _bank(app))
    codes = {r["code"] for r in dossier["decisions"][0]["reasons"] if r["kind"] == "MANUAL"}
    assert {"grade_d", "tax_arrears", "director_changed"} <= codes

    a.call("POST", _bank(app, "/decision"), json={"action": "REQUEST_INFO", "info_items": []}, expect=422)
    a.call(
        "POST",
        _bank(app, "/decision"),
        json={
            "action": "REQUEST_INFO",
            "info_items": [{"type": "DOCUMENT", "doc_type": "OTHER", "label": "Справка об уплате налогов"}],
            "message_to_client": "Нужна справка",
        },
    )
    app = c.get(app["id"])
    assert app["status"] == "NEEDS_INFO"
    assert app["info_request"]["requested_by"] == "Волкова Ирина Андреевна"
    c.demo_doc(app["id"], "OTHER")
    c.call("POST", _url(app, "/info-response"), json={})
    drain()
    assert c.get(app["id"])["status"] == "MANUAL_REVIEW"  # причины ручного рассмотрения остались

    a.call("POST", _bank(app, "/decision"), json={"action": "DECLINE"}, expect=422)
    a.call(
        "POST",
        _bank(app, "/decision"),
        json={
            "action": "DECLINE",
            "reason": "Налоговый долг и смена директора",
            "message_to_client": "Сейчас не можем одобрить кредит",
        },
    )
    app = c.get(app["id"])
    assert app["status"] == "DECLINED"
    # причина — в объяснении решения, сообщение специалиста — отдельно, без повтора
    assert app["decision"]["reasons"] == ["Налоговый долг и смена директора"]
    assert app["decision"]["message"] == "Сейчас не можем одобрить кредит"
    # при отказе не показываем рейтинг и сильные стороны
    assert app["decision"]["grade"] is None and app["decision"]["positives"] == []
    assert app["events"][-1]["title"] == "Отказ: налоговый долг и смена директора"
    a.call("POST", _bank(app, "/decision"), json={"action": "DECLINE", "reason": "повтор"}, expect=409)


# --------------------------------------------------------------------------- проверки и права


def test_submit_validation_and_signing_rights(login):
    u = login("smirnov")
    app = u.draft("EXPRESS", "OTHER", 100_000, 48)
    problems = u.call("POST", _url(app, "/submit"), expect=422)["detail"]["problems"]
    assert any(p.startswith("Сумма") for p in problems)
    assert any(p.startswith("Срок") for p in problems)
    assert any("согласия" in p for p in problems)

    accountant = login("orlova")
    accountant.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"}, expect=403)
    accountant.call("POST", _url(app, "/submit"), expect=403)
    accountant.call("POST", _url(app, "/request-signature"))
    notes = u.call("GET", "/api/v1/notifications")
    assert any(n["application_id"] == app["id"] and "подпись" in n["title"] for n in notes["items"])


def test_wrong_sign_code_attempt_limit_and_reuse(login):
    u = login("smirnov")
    app = u.draft("EXPRESS", "OTHER", 500_000, 6)
    ch = u.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"})
    wrong = "000000" if ch["demo_code"] != "000000" else "111111"
    body = {"challenge_id": ch["challenge_id"], "code": wrong}
    assert "Неверный" in u.call("POST", _url(app, "/consents"), json=body, expect=422)["detail"]
    for _ in range(4):
        u.call("POST", _url(app, "/consents"), json=body, expect=422)
    u.call("POST", _url(app, "/consents"), json={**body, "code": ch["demo_code"]}, expect=429)

    ch2 = u.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"})
    good = {"challenge_id": ch2["challenge_id"], "code": ch2["demo_code"]}
    assert len(u.call("POST", _url(app, "/consents"), json=good)["consents"]) == 2
    u.call("POST", _url(app, "/consents"), json=good, expect=409)
    u.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONTRACT"}, expect=409)


def test_documents_upload_download_and_delete(login):
    u = login("smirnov")
    app = u.draft("INVESTMENT", "EQUIPMENT", 2_000_000, 24)
    pdf = b"%PDF-1.4\n% synthetic invoice\n"
    doc = u.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "SUPPLIER_INVOICE"},
        files={"file": ("invoice.pdf", pdf, "application/pdf")},
    )
    assert doc["doc_type_label"] == "Счёт или спецификация поставщика"
    assert u.call("GET", _url(app, f"/documents/{doc['id']}")).content == pdf
    assert u.get(app["id"])["required_documents"][0]["attached"] is True

    u.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "OTHER"},
        files={"file": ("tool.exe", b"MZ", "application/x-msdownload")},
        expect=422,
    )
    u.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "PASSPORT"},
        files={"file": ("a.pdf", pdf, "application/pdf")},
        expect=422,
    )
    u.call("DELETE", _url(app, f"/documents/{doc['id']}"), expect=204)
    assert u.get(app["id"])["documents"] == []


# --------------------------------------------------------------------------- отказоустойчивость и конфликты


def test_bki_outage_goes_to_analyst_and_recovers_after_rerun(login):
    a = login("analyst")
    a.call("POST", "/api/v1/bank/integrations/faults", json={"service": "BKI", "mode": "down", "duration_seconds": 60})
    assert a.call("GET", "/api/v1/bank/integrations/faults")["faults"]["BKI"]["mode"] == "down"

    c = login("smirnov")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 500_000, 6)
    assert app["status"] == "MANUAL_REVIEW"
    assert app["sources_received"] == ["EGRUL", "FNS"]  # интерфейс показывает, на каком источнике остановились
    dossier = a.call("GET", _bank(app))
    assert dossier["decisions"][0]["reasons"][0]["code"] == "integration_unavailable"
    calls = dossier["integration_calls"]
    bki = [x for x in calls if x["service"] == "BKI"]
    assert len(bki) == 3 and all(x["status"] == "ERROR" and x["http_status"] == 503 for x in bki)
    assert {x["attempt"] for x in bki} == {1, 2, 3}
    assert len([x for x in calls if x["service"] == "EGRUL"]) == 1  # успешные ответы не запрашиваются повторно

    a.call("POST", "/api/v1/bank/integrations/faults", json={"service": "BKI", "mode": "ok"})
    a.call("POST", _bank(app, "/rerun"))
    drain()
    app = c.get(app["id"])
    assert app["status"] == "OFFER_READY" and app["sources_received"] == ["EGRUL", "FNS", "BKI"]


def test_actions_in_wrong_status_return_409(login):
    u = login("smirnov")
    app = u.prepare_and_submit("EXPRESS", "OTHER", 400_000, 6)
    assert app["status"] == "OFFER_READY"
    u.call("POST", _url(app, "/submit"), expect=409)
    u.call("PATCH", _url(app), json={"amount": 450_000}, expect=409)
    u.call("POST", _url(app, "/info-response"), json={}, expect=409)
    u.call("POST", _url(app, "/cancel"), expect=409)
    a = login("analyst")
    a.call("POST", _bank(app, "/decision"), json={"action": "DECLINE", "reason": "x"}, expect=409)
    a.call("POST", _bank(app, "/rerun"), expect=409)


def test_concurrent_modification_returns_409(login, monkeypatch):
    import app.api.applications as applications_api
    from app.db import engine

    u = login("smirnov")
    draft = u.draft("EXPRESS", "OTHER", 500_000, 6)
    original = applications_api.get_client_application

    def concurrent_edit(db, user, app_id):
        loaded, member = original(db, user, app_id)
        with engine.begin() as conn:  # другой запрос успел изменить заявку
            conn.execute(text("UPDATE applications SET version = version + 1 WHERE id = :id"), {"id": app_id})
        return loaded, member

    monkeypatch.setattr(applications_api, "get_client_application", concurrent_edit)
    resp = u.call("PATCH", _url(draft), json={"purpose_details": "Параллельная правка"}, expect=409)
    assert "изменилась" in resp["detail"]


def test_cancel_draft(login):
    u = login("smirnov")
    app = u.draft("EXPRESS", "OTHER", 500_000, 6)
    assert u.call("POST", _url(app, "/cancel"))["status"] == "CANCELLED"
    u.call("POST", _url(app, "/cancel"), expect=409)


# --------------------------------------------------------------------------- фоновые задачи воркера


def test_offer_expiry(login):
    from app.db import SessionLocal
    from app.models import Offer
    from app.worker.runner import housekeeping

    u = login("smirnov")
    app = u.prepare_and_submit("EXPRESS", "OTHER", 300_000, 3)
    assert app["status"] == "OFFER_READY"
    with SessionLocal() as db:
        db.execute(
            update(Offer).where(Offer.application_id == app["id"]).values(valid_until=datetime(2000, 1, 1, tzinfo=UTC))
        )
        db.commit()
    housekeeping()
    app = u.get(app["id"])
    assert app["status"] == "EXPIRED" and app["offers"][0]["status"] == "EXPIRED"
    u.call("POST", _url(app, f"/offers/{app['offers'][0]['id']}/accept"), expect=409)


def test_stale_running_job_is_returned_to_queue(login):
    from app.db import SessionLocal
    from app.models import Application, PipelineJob
    from app.worker.runner import housekeeping

    draft = login("smirnov").draft("EXPRESS", "OTHER", 500_000, 6)
    with SessionLocal() as db:
        job = PipelineJob(
            application_id=draft["id"],
            stage="SCORING",
            status="RUNNING",
            attempts=1,
            max_attempts=3,
            run_after=datetime(2000, 1, 1, tzinfo=UTC),
            locked_by="dead-worker",
            locked_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        db.add(job)
        db.commit()
        job_id = job.id
    housekeeping()
    with SessionLocal() as db:
        assert db.get(PipelineJob, job_id).status == "PENDING"
    drain()  # этап идемпотентен: заявка не в статусе скоринга, повторный запуск ничего не меняет
    with SessionLocal() as db:
        assert db.get(PipelineJob, job_id).status == "DONE"
        assert db.scalar(select(Application.status).where(Application.id == draft["id"])) == "DRAFT"


# --------------------------------------------------------------------------- банк: реестр, мониторинг, параметры


def test_bank_registry_filters(login):
    u = login("smirnov")
    u.accept_and_sign(u.prepare_and_submit("EXPRESS", "OTHER", 300_000, 3))
    a = login("analyst")
    rows = a.call("GET", "/api/v1/bank/applications", params={"scope": "all"})
    assert rows and all(r["status"] != "DRAFT" for r in rows)
    issued = a.call("GET", "/api/v1/bank/applications", params={"scope": "all", "status": "ISSUED"})
    assert issued and all(r["status"] == "ISSUED" for r in issued)
    found = a.call("GET", "/api/v1/bank/applications", params={"scope": "all", "q": "Северный"})
    assert found and all("Северный" in r["company_name"] for r in found)
    # «%» и «_» — обычные символы, а не шаблон «всё подряд»
    for wildcard in ("%", "_", "Север%хлеб"):
        assert a.call("GET", "/api/v1/bank/applications", params={"scope": "all", "q": wildcard}) == []
    # пустой запрос — без фильтра
    assert a.call("GET", "/api/v1/bank/applications", params={"scope": "all", "q": "   "})
    mine = a.call("GET", "/api/v1/bank/applications", params={"scope": "mine"})
    assert all(r["assignee"] == "Волкова Ирина Андреевна" for r in mine)


def test_dashboard_settings_health_and_metrics(api, login):
    login("smirnov").prepare_and_submit("EXPRESS", "OTHER", 300_000, 3)
    a = login("analyst")
    dash = a.call("GET", "/api/v1/bank/dashboard")
    assert dash["total"] > 0
    assert {"funnel", "by_status", "auto_decision_share", "integrations_last_hour", "recent_events"} <= set(dash)

    risk = login("risk")
    changed = risk.call("PUT", "/api/v1/bank/settings/key_rate", json={"value": 16})
    try:
        assert changed["value"] == 16.0 and changed["updated_by"] == "Белов Сергей Николаевич"
        assert login("smirnov").call("GET", "/api/v1/dictionaries")["key_rate"] == 16.0
    finally:
        risk.call("PUT", "/api/v1/bank/settings/key_rate", json={"value": 14})
    risk.call("PUT", "/api/v1/bank/settings/key_rate", json={"value": "abc"}, expect=422)
    risk.call("PUT", "/api/v1/bank/settings/key_rate", json={"value": 80}, expect=422)
    risk.call("PUT", "/api/v1/bank/settings/unknown", json={"value": 1}, expect=404)

    assert api.get("/health/live").json() == {"status": "ok"}
    assert api.get("/health/ready").json()["db"] == "up"
    metrics = api.get("/metrics")
    assert metrics.status_code == 200 and "pipeline_queue_depth" in metrics.text
    assert "X-Request-ID" in api.get("/health/live").headers
