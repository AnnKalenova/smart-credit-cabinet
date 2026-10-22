"""Правила процесса: права подписи на юридически значимых шагах, сроки действия предложений,
залог, документы по запросу банка, повторная заявка, ограничения на загрузку файлов."""

from datetime import UTC, datetime

from sqlalchemy import update

from tests.conftest import drain


def _url(app: dict, suffix: str = "") -> str:
    return f"/api/v1/applications/{app['id']}{suffix}"


def _bank(app: dict, suffix: str = "") -> str:
    return f"/api/v1/bank/applications/{app['id']}{suffix}"


def _expire_offers(app_id: str) -> None:
    from app.db import SessionLocal
    from app.models import Offer

    with SessionLocal() as db:
        db.execute(
            update(Offer).where(Offer.application_id == app_id).values(valid_until=datetime(2000, 1, 1, tzinfo=UTC))
        )
        db.commit()


# --------------------------------------------------------------------------- права подписи


def test_only_signer_chooses_declines_or_returns_to_offer(login):
    owner, accountant = login("smirnov"), login("orlova")
    app = owner.prepare_and_submit("EXPRESS", "OTHER", 300_000, 3)
    assert app["status"] == "OFFER_READY"
    offer = app["offers"][0]

    seen = accountant.get(app["id"])["permissions"]
    assert seen["can_accept"] is False and seen["can_request_signature"] is True and seen["can_cancel"] is False
    denied = accountant.call("POST", _url(app, f"/offers/{offer['id']}/accept"), expect=403)
    assert denied["detail"].startswith("Выбрать условия кредита может руководитель")
    accountant.call("POST", _url(app, "/offers/decline"), expect=403)
    accountant.call("POST", _url(app, "/cancel"), expect=409)  # после решения банка заявку уже не отозвать
    accountant.call("POST", _url(app, "/request-signature"))
    notes = owner.call("GET", "/api/v1/notifications")["items"]
    assert any(n["application_id"] == app["id"] and "выбрать вариант" in n["body"] for n in notes)

    assert owner.get(app["id"])["permissions"]["can_accept"] is True
    signing = owner.call("POST", _url(app, f"/offers/{offer['id']}/accept"))
    assert signing["status"] == "SIGNING"
    rate = f"{offer['rate']:.1f}".replace(".", ",")
    assert signing["events"][-1]["title"] == f"Выбрано предложение: 300\u00a0000\u00a0₽ на 3 мес. под {rate}%"
    accountant.call("POST", _url(app, "/signing/back"), expect=403)
    accountant.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONTRACT"}, expect=403)


def test_draft_author_cancels_own_draft_but_not_a_submitted_application(login):
    owner, accountant = login("smirnov"), login("orlova")
    own = accountant.draft("EXPRESS", "OTHER", 500_000, 6)
    assert own["permissions"]["can_cancel"] is True
    cancelled = accountant.call("POST", _url(own, "/cancel"))
    assert cancelled["status"] == "CANCELLED" and cancelled["events"][-1]["title"] == "Черновик отменён"

    foreign = owner.draft("EXPRESS", "OTHER", 500_000, 6)
    assert accountant.get(foreign["id"])["permissions"]["can_cancel"] is False
    accountant.call("POST", _url(foreign, "/cancel"), expect=403)

    owner.sign_consents(foreign["id"])
    owner.submit(foreign["id"])  # заявка в банке, конвейер ещё не запущен
    denied = accountant.call("POST", _url(foreign, "/cancel"), expect=403)
    assert denied["detail"].startswith("Отозвать заявку может руководитель")
    assert owner.call("POST", _url(foreign, "/cancel"))["status"] == "CANCELLED"
    drain()  # этап очереди по отозванной заявке ничего не меняет
    assert owner.get(foreign["id"])["status"] == "CANCELLED"


# --------------------------------------------------------------------------- сроки действия предложения


def test_contract_cannot_be_signed_after_offer_expiry(login):
    u = login("smirnov")
    app = u.prepare_and_submit("EXPRESS", "OTHER", 300_000, 3)
    u.call("POST", _url(app, f"/offers/{app['offers'][0]['id']}/accept"))
    ch = u.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONTRACT"})
    _expire_offers(app["id"])

    resp = u.call(
        "POST", _url(app, "/sign"), json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]}, expect=409
    )
    assert "истёк" in resp["detail"]
    app = u.get(app["id"])
    assert app["status"] == "EXPIRED" and app["loan"] is None
    assert app["permissions"]["can_reapply"] is True
    assert app["events"][-1]["title"] == "Срок действия предложения истёк до подписания договора"


def test_worker_expires_unsigned_contract(login):
    from app.worker.runner import housekeeping

    u = login("smirnov")
    app = u.prepare_and_submit("EXPRESS", "OTHER", 300_000, 3)
    u.call("POST", _url(app, f"/offers/{app['offers'][0]['id']}/accept"))
    _expire_offers(app["id"])
    housekeeping()
    app = u.get(app["id"])
    assert app["status"] == "EXPIRED"
    notes = u.call("GET", "/api/v1/notifications")["items"]
    assert any(n["application_id"] == app["id"] and "срок предложения истёк" in n["title"] for n in notes)


def test_back_to_offers_after_expiry_closes_application(login):
    u = login("kuznetsova")
    app = u.prepare_and_submit("EXPRESS", "EXPANSION", 1_000_000, 12)
    assert len(app["offers"]) == 2
    u.call("POST", _url(app, f"/offers/{app['offers'][0]['id']}/accept"))
    _expire_offers(app["id"])
    resp = u.call("POST", _url(app, "/signing/back"), expect=409)
    assert "истёк" in resp["detail"]
    assert u.get(app["id"])["status"] == "EXPIRED"


# --------------------------------------------------------------------------- залог и документы


def test_offered_collateral_needs_description_and_appears_in_offer(login):
    u = login("smirnov")
    app = u.draft("WORKING_CAPITAL", "WORKING_CAPITAL", 3_000_000, 12, has_collateral=True)
    u.sign_consents(app["id"])
    problems = u.call("POST", _url(app, "/submit"), expect=422)["detail"]["problems"]
    assert "Опишите имущество, которое будет в залоге" in problems

    u.call("PATCH", _url(app), json={"collateral_description": "Склад 600 м² в собственности компании"})
    u.submit(app["id"])
    drain()
    app = u.get(app["id"])
    assert app["status"] == "OFFER_READY"
    offer = app["offers"][0]
    assert "Залог имущества, указанного в заявке" in offer["conditions"]
    assert {"label": "Скидка за залог", "value": -1.0} in offer["pricing"]["parts"]
    assert offer["rate"] == 17.5  # 18,5% без залога


def test_requested_document_must_be_uploaded_after_request(login):
    c, a = login("nikitin"), login("analyst")
    app = c.prepare_and_submit("WORKING_CAPITAL", "CONTRACT", 8_000_000, 18, docs=["CONTRACT_COPY"])
    assert app["status"] == "MANUAL_REVIEW"
    a.call(
        "POST",
        _bank(app, "/decision"),
        json={
            "action": "REQUEST_INFO",
            "info_items": [
                {"type": "DOCUMENT", "doc_type": "CONTRACT_COPY", "label": "Контракт со всеми приложениями"}
            ],
        },
    )
    # контракт приложен ещё в черновике — специалист просит новую версию, старая не засчитывается
    problems = c.call("POST", _url(app, "/info-response"), json={}, expect=422)["detail"]["problems"]
    assert problems == ["Приложите: Контракт со всеми приложениями"]
    c.demo_doc(app["id"], "CONTRACT_COPY")
    assert c.call("POST", _url(app, "/info-response"), json={})["status"] == "PRECHECK"
    drain()


def test_analyst_offer_must_fit_application_purpose(login):
    c, a = login("nikitin"), login("analyst")
    app = c.prepare_and_submit("WORKING_CAPITAL", "CONTRACT", 8_000_000, 18, docs=["CONTRACT_COPY"])
    assert app["status"] == "MANUAL_REVIEW"
    hints = {h["product_code"] for h in a.call("GET", _bank(app))["pricing_hints"]}
    assert hints == {"WORKING_CAPITAL"}  # под «Исполнить контракт» подходит только оборотный кредит
    resp = a.call(
        "POST",
        _bank(app, "/decision"),
        json={"action": "APPROVE", "offers": [{"product_code": "INVESTMENT", "amount": 5_000_000, "term_months": 24}]},
        expect=422,
    )
    assert resp["detail"] == "Продукт «Инвестиционный кредит» не подходит для цели заявки"
    a.call(
        "POST",
        _bank(app, "/decision"),
        json={
            "action": "APPROVE",
            "offers": [{"product_code": "WORKING_CAPITAL", "amount": 5_000_000, "term_months": 18}],
        },
    )
    assert c.get(app["id"])["status"] == "OFFER_READY"


def test_reapply_copies_documents_into_new_application(login):
    from app.db import SessionLocal
    from app.models import ApplicationDocument

    u = login("lebedev")
    draft = u.draft("INVESTMENT", "EQUIPMENT", 3_000_000, 36, collateral_description="Фрезерный станок")
    u.demo_doc(draft["id"], "SUPPLIER_INVOICE")
    u.call("POST", _url(draft, "/cancel"))

    new = u.call("POST", _url(draft, "/reapply"))
    assert new["parent_id"] == draft["id"] and new["collateral_description"] == "Фрезерный станок"
    assert [d["doc_type"] for d in new["documents"]] == ["SUPPLIER_INVOICE"]
    assert new["consents"] == []  # согласия подписываются заново
    assert any(e["title"].startswith("Перенесены документы из заявки") for e in new["events"])

    copied = new["documents"][0]
    original = u.get(draft["id"])["documents"][0]
    assert copied["id"] != original["id"]
    assert (
        u.call("GET", _url(new, f"/documents/{copied['id']}")).content
        == u.call("GET", _url(draft, f"/documents/{original['id']}")).content
    )
    with SessionLocal() as db:
        keys = {db.get(ApplicationDocument, d["id"]).storage_key for d in (copied, original)}
    assert len(keys) == 2  # у каждой заявки свой файл
    u.call("DELETE", _url(new, f"/documents/{copied['id']}"), expect=204)
    assert u.call("GET", _url(draft, f"/documents/{original['id']}")).content


# --------------------------------------------------------------------------- файлы и лимиты


def test_upload_checks_file_signature_and_infers_type(login):
    u = login("smirnov")
    app = u.draft("EXPRESS", "OTHER", 500_000, 6)

    fake = u.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "OTHER"},
        files={"file": ("report.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")},
        expect=422,
    )
    assert "не соответствует его формату" in fake["detail"]
    u.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "OTHER"},
        files={"file": ("notes.txt", b"text\x00with nul", "text/plain")},
        expect=422,
    )
    # браузер прислал общий тип — формат определяется по расширению и проверяется по содержимому
    doc = u.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "OTHER"},
        files={"file": ("scan.PNG", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "application/octet-stream")},
    )
    assert doc["content_type"] == "image/png"
    docx = u.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "OTHER"},
        files={
            "file": (
                "contract.docx",
                b"PK\x03\x04" + b"\x00" * 26,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert docx["file_name"] == "contract.docx"


def test_upload_size_limit(login, monkeypatch):
    from app.api import applications as applications_api

    monkeypatch.setattr(applications_api.settings, "max_upload_mb", 1)
    u = login("smirnov")
    app = u.draft("EXPRESS", "OTHER", 500_000, 6)
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024)
    resp = u.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "OTHER"},
        files={"file": ("big.pdf", big, "application/pdf")},
        expect=413,
    )
    assert resp["detail"] == "Файл больше 1 МБ"


def test_sign_code_requests_are_rate_limited(login):
    u = login("morozov")
    app = u.draft("OVERDRAFT", "CASH_GAP", 300_000, 12)
    for _ in range(10):
        u.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"})
    resp = u.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"}, expect=429)
    assert "10 минут" in resp["detail"]


def test_wrong_status_message_names_current_status(login):
    u = login("smirnov")
    app = u.prepare_and_submit("EXPRESS", "OTHER", 300_000, 3)
    resp = u.call("POST", _url(app, "/submit"), expect=409)
    assert resp["detail"] == "Действие недоступно: заявка в статусе «Готово предложение». Обновите страницу"


def test_collateral_flag_follows_product(login):
    u = login("smirnov")
    app = u.draft("INVESTMENT", "EQUIPMENT", 2_000_000, 24)
    assert app["has_collateral"] is True  # инвестиционный кредит — только с залогом
    switched = u.call(
        "PATCH", _url(app), json={"product_code": "EXPRESS", "purpose": "OTHER", "amount": 500_000, "term_months": 6}
    )
    assert switched["has_collateral"] is False  # флаг от прежнего продукта сброшен
    assert u.call("PATCH", _url(app), json={"has_collateral": True})["has_collateral"] is False

    u.sign_consents(app["id"])
    u.submit(app["id"])
    drain()
    app = u.get(app["id"])
    assert app["status"] == "OFFER_READY"
    offer = app["offers"][0]
    assert not any("Залог" in c for c in offer["conditions"])
    assert all(p["label"] != "Скидка за залог" for p in offer["pricing"]["parts"])


def test_only_signer_changes_amount_in_answer_to_bank(login):
    owner, accountant = login("smirnov"), login("orlova")
    # заявленная выручка вдвое больше поступлений на счёт — банк просит подтверждение
    app = owner.prepare_and_submit("EXPRESS", "OTHER", 500_000, 6, declared_revenue=120_000_000)
    assert app["status"] == "NEEDS_INFO"
    denied = accountant.call("POST", _url(app, "/info-response"), json={"fields": {"amount": 400_000}}, expect=403)
    assert denied["detail"].startswith("Изменить сумму или срок кредита может руководитель")
    # на сам запрос бухгалтер ответить может: приложить выписку и пояснить
    accountant.demo_doc(app["id"], "BANK_STATEMENT")
    answered = accountant.call("POST", _url(app, "/info-response"), json={"comment": "Часть выручки — в другом банке"})
    assert answered["status"] == "PRECHECK" and answered["amount"] == 500_000
    drain()
