"""Защита и устойчивость: что видит банк, демо-функции вне демо-режима, токены, ввод с NUL, имена файлов,
подпись при параллельных запросах, сбой выдачи, воркер, тексты для клиента и банка."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import delete, func, select, update

from tests.conftest import drain


def _url(app: dict, suffix: str = "") -> str:
    return f"/api/v1/applications/{app['id']}{suffix}"


def _bank(app: dict, suffix: str = "") -> str:
    return f"/api/v1/bank/applications/{app['id']}{suffix}"


def _manual_review(login) -> dict:
    """Заявка на ручном рассмотрении с рассчитанным рейтингом (крупная инвестиционная сумма)."""
    return login("lebedev").prepare_and_submit(
        "INVESTMENT",
        "EQUIPMENT",
        25_000_000,
        60,
        docs=["SUPPLIER_INVOICE", "FIN_STATEMENTS"],
        collateral_description="Обрабатывающий центр с ЧПУ",
    )


# --------------------------------------------------------------------------- что видит банк


def test_bank_never_sees_unsent_applications(login):
    c, a = login("smirnov"), login("analyst")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    doc = c.demo_doc(app["id"], "OTHER")
    c.call("POST", _url(app, "/cancel"))
    # отменённый черновик — данные клиента без подписанных согласий: банку не видны
    a.call("GET", _bank(app), expect=404)
    a.call("GET", _bank(app, f"/documents/{doc['id']}"), expect=404)
    assert a.call("GET", "/api/v1/bank/applications", params={"scope": "all", "q": app["number"]}) == []
    recent = a.call("GET", "/api/v1/bank/dashboard")["recent_events"]
    assert all(e["application_id"] != app["id"] for e in recent)
    assert c.get(app["id"])["status_view"]["title"] == "Черновик отменён"


# --------------------------------------------------------------------------- демо-функции


def test_demo_features_are_off_outside_demo_mode(api, login, monkeypatch):
    from app.config import get_settings

    c, a = login("smirnov"), login("analyst")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    c.call("POST", _url(app, "/documents/demo"), params={"doc_type": "OTHER"}, expect=404)
    a.call("GET", "/api/v1/bank/integrations/faults", expect=404)
    a.call("POST", "/api/v1/bank/integrations/faults", json={"service": "BKI", "mode": "down"}, expect=404)
    assert api.get("/api/v1/auth/demo-accounts").status_code == 404
    challenge = c.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"})
    assert challenge["demo_code"] is None


def test_seed_creates_no_demo_users_outside_demo_mode(monkeypatch):
    import app.seed as seed_module
    from app.config import get_settings

    class Db:
        def __init__(self):
            self.added = []

        def execute(self, *a, **k):
            return None

        def get(self, *a, **k):
            return object()

        def add(self, obj):
            self.added.append(obj)

        def scalar(self, *a, **k):
            raise AssertionError("вне демо-режима пользователи не проверяются и не создаются")

        def commit(self):
            pass

    monkeypatch.setattr(get_settings(), "demo_mode", False)
    monkeypatch.setattr(seed_module, "sync_products", lambda db: None)
    db = Db()
    assert seed_module.seed(db) is False
    assert db.added == []


# --------------------------------------------------------------------------- токены


def test_tokens_need_expiry_and_valid_subject(api, login):
    from app.config import get_settings

    secret = get_settings().jwt_secret
    user_id = login("smirnov").me["user"]["id"]
    now = datetime.now(UTC)

    def status(payload: dict) -> int:
        token = jwt.encode(payload, secret, algorithm="HS256")
        return api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code

    base = {"iss": "smart-credit-cabinet", "iat": now}
    assert status({**base, "sub": user_id, "exp": now + timedelta(minutes=5)}) == 200
    assert status({**base, "sub": user_id}) == 401  # без срока действия
    assert status({**base, "sub": "not-a-uuid", "exp": now + timedelta(minutes=5)}) == 401
    assert status({**base, "exp": now + timedelta(minutes=5)}) == 401
    assert status({**base, "sub": str(uuid.uuid4()), "exp": now + timedelta(minutes=5)}) == 401


# --------------------------------------------------------------------------- ввод


def test_nul_characters_get_422_not_500(api, login):
    c, a, r = login("smirnov"), login("analyst"), login("risk")
    resp = api.post("/api/v1/auth/login", json={"login": "smirnov\x00", "password": "x"})
    assert resp.status_code == 422 and resp.json()["detail"]["problems"] == ["недопустимый символ в тексте"]
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    c.call("PATCH", _url(app), json={"contact_name": "Иванов\x00"}, expect=422)
    c.call("POST", _url(app, "/messages"), json={"text": "a\x00b"}, expect=422)
    bad = a.call("GET", "/api/v1/bank/applications", params={"q": "abc\x00"}, expect=422)
    assert bad["detail"]["problems"] == ["недопустимые символы в данных"]
    r.call("PUT", "/api/v1/bank/settings/key_rate_note", json={"value": "x\x00"}, expect=422)


def test_blank_messages_and_tiny_amounts_are_rejected(login):
    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    resp = c.call("POST", _url(app, "/messages"), json={"text": "   "}, expect=422)
    assert resp["detail"]["problems"] == ["Сообщение: не может быть пустым"]
    resp = c.call("PATCH", _url(app), json={"amount": 1e-300}, expect=422)
    assert resp["detail"]["problems"] == ["Сумма: должно быть не меньше 1"]
    sent = c.prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)  # переписка — по отправленной заявке
    assert c.call("POST", _url(sent, "/messages"), json={"text": "  Добрый день  "})["text"] == "Добрый день"


def test_framework_errors_are_in_russian(api, login):
    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    resp = api.post(
        _url(app, "/documents"),
        headers={**c.headers, "Content-Type": "multipart/form-data"},  # без boundary
        content=b"garbage",
    )
    assert resp.status_code == 400 and resp.json()["detail"] == "Не удалось прочитать запрос — проверьте формат данных"
    resp = c.call("POST", _url(app, "/documents"), data={"doc_type": "OTHER", "file": "not-a-file"}, expect=422)
    assert resp["detail"]["problems"] == ["Файл: приложите файл"]
    assert api.get("/api/v1/no-such-route").json()["detail"] == "Не найдено"
    resp = c.call("GET", "/api/v1/applications/not-a-uuid", expect=422)
    assert resp["detail"]["problems"] == ["Заявка: некорректный идентификатор"]


def test_request_id_from_outside_is_validated(api):
    assert api.get("/health/live", headers={"X-Request-ID": "abc-123"}).headers["X-Request-ID"] == "abc-123"
    for bad in ("bad id with spaces", "x" * 100, "<script>"):
        echoed = api.get("/health/live", headers={"X-Request-ID": bad}).headers["X-Request-ID"]
        assert echoed != bad and len(echoed) == 32


# --------------------------------------------------------------------------- файлы


def test_dangerous_file_names_are_neutralized(login):
    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    hta = c.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "OTHER"},
        files={"file": ("Счёт поставщика.hta", "Добрый день".encode(), "text/plain")},
    )
    assert hta["file_name"] == "Счёт поставщика.hta.txt"
    download = c.call("GET", _url(app, f"/documents/{hta['id']}"))
    assert ".hta.txt" in download.headers["content-disposition"]
    rtl = c.call(
        "POST",
        _url(app, "/documents"),
        data={"doc_type": "OTHER"},
        files={"file": ("‮gnp.exe", b"\x89PNG\r\n\x1a\n....", "image/png")},
    )
    assert rtl["file_name"] == "gnp.exe.png"


def test_missing_file_gives_404_and_delete_keeps_db_consistent(login):
    from app.db import SessionLocal
    from app.models import ApplicationDocument
    from app.services import storage

    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    doc = c.demo_doc(app["id"], "OTHER")
    with SessionLocal() as db:
        key = db.get(ApplicationDocument, doc["id"]).storage_key
    storage.path_for(key).unlink()
    resp = c.call("GET", _url(app, f"/documents/{doc['id']}"), expect=404)
    assert "Файл не найден" in resp["detail"]
    c.call("DELETE", _url(app, f"/documents/{doc['id']}"), expect=204)
    assert c.get(app["id"])["documents"] == []


def test_documents_per_application_are_limited(login, monkeypatch):
    import app.api.applications as applications

    monkeypatch.setattr(applications, "MAX_DOCUMENTS_PER_APPLICATION", 2)
    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    c.demo_doc(app["id"], "OTHER")
    c.demo_doc(app["id"], "OTHER")
    resp = c.call("POST", _url(app, "/documents/demo"), params={"doc_type": "OTHER"}, expect=409)
    assert "не больше 2 файлов" in resp["detail"]


# --------------------------------------------------------------------------- подпись при параллельных запросах


def test_parallel_wrong_codes_respect_attempt_limit(api, login):
    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    ch = c.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"})
    wrong = "000000" if ch["demo_code"] != "000000" else "111111"

    def attempt(_):
        body = {"challenge_id": ch["challenge_id"], "code": wrong}
        return api.post(_url(app, "/consents"), headers=c.headers, json=body).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(attempt, range(12)))
    assert codes.count(422) == 5 and codes.count(429) == 7
    # после пяти неверных попыток верный код уже не принимается
    c.call(
        "POST",
        _url(app, "/consents"),
        json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]},
        expect=429,
    )


def test_parallel_valid_code_signs_consents_once(api, login):
    from app.db import SessionLocal
    from app.models import ApplicationEvent, Consent

    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    ch = c.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"})

    def attempt(_):
        body = {"challenge_id": ch["challenge_id"], "code": ch["demo_code"]}
        return api.post(_url(app, "/consents"), headers=c.headers, json=body).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(attempt, range(6)))
    assert codes.count(200) == 1 and codes.count(409) == 5
    with SessionLocal() as db:
        consents = db.scalar(select(func.count()).select_from(Consent).where(Consent.application_id == app["id"]))
        signed = db.scalar(
            select(func.count())
            .select_from(ApplicationEvent)
            .where(ApplicationEvent.application_id == app["id"], ApplicationEvent.event_type == "CONSENTS_SIGNED")
        )
    assert consents == 2 and signed == 1


def test_parallel_code_requests_respect_limit(api, login):
    c = login("kuznetsova")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)

    def attempt(_):
        return api.post(_url(app, "/sign-challenges"), headers=c.headers, json={"purpose": "CONSENTS"}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(attempt, range(16)))
    assert codes.count(200) == 10 and codes.count(429) == 6


# --------------------------------------------------------------------------- вход


def test_failures_from_another_address_do_not_lock_the_user(api):
    from app.db import SessionLocal
    from app.models import LoginAttempt

    with SessionLocal() as db:
        db.add_all([LoginAttempt(login="nikitin", success=False, ip="203.0.113.7") for _ in range(12)])
        db.commit()
    try:
        # чужой адрес подбирал пароль — пользователь со своего адреса входит как обычно
        assert api.post("/api/v1/auth/login", json={"login": "nikitin", "password": "demo1234"}).status_code == 200
        with SessionLocal() as db:
            db.add_all([LoginAttempt(login="nikitin", success=False, ip=f"198.51.100.{i}") for i in range(50)])
            db.commit()
        # подбор с множества адресов закрывает вход по логину для всех
        locked = api.post("/api/v1/auth/login", json={"login": "nikitin", "password": "demo1234"})
        assert locked.status_code == 429
    finally:
        with SessionLocal() as db:
            db.execute(delete(LoginAttempt).where(LoginAttempt.login == "nikitin"))
            db.commit()


def test_one_address_cannot_spray_many_logins(api):
    from app.db import SessionLocal
    from app.models import LoginAttempt

    with SessionLocal() as db:
        db.add_all([LoginAttempt(login=f"guess-{i}", success=False, ip="testclient") for i in range(100)])
        db.commit()
    try:
        resp = api.post("/api/v1/auth/login", json={"login": "morozov", "password": "demo1234"})
        assert resp.status_code == 429 and "с вашего адреса" in resp.json()["detail"]
    finally:
        with SessionLocal() as db:
            db.execute(delete(LoginAttempt).where(LoginAttempt.login.like("guess-%")))
            db.commit()


# --------------------------------------------------------------------------- решения банка


def test_only_assignee_or_risk_manager_decides(login):
    app = _manual_review(login)
    a, r = login("analyst"), login("risk")
    r.call("POST", _bank(app, "/assign"))
    resp = a.call("POST", _bank(app, "/decision"), json={"action": "DECLINE", "reason": "Причина"}, expect=409)
    assert resp["detail"].startswith("Заявку рассматривает ")
    assert resp["detail"].endswith(": решение принимает этот специалист или риск-менеджер")
    requested = {"product_code": "INVESTMENT", "amount": 25_000_000, "term_months": 60}
    alternative = {"product_code": "INVESTMENT", "amount": 20_000_000, "term_months": 60}
    r.call("POST", _bank(app, "/decision"), json={"action": "APPROVE", "offers": [requested, alternative]})
    view = login("lebedev").get(app["id"])
    # среди вариантов есть запрошенные условия — это одобрение, а не встречное предложение
    assert view["decision"]["outcome"] == "APPROVE"


def test_approval_needs_source_data(login):
    a = login("analyst")
    a.call(
        "POST", "/api/v1/bank/integrations/faults", json={"service": "EGRUL", "mode": "down", "duration_seconds": 60}
    )
    app = login("smirnov").prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)
    assert app["status"] == "MANUAL_REVIEW"
    reason = a.call("GET", _bank(app))["decisions"][0]["reasons"][0]["text"]
    assert reason == "Не удалось получить данные из ЕГРЮЛ/ЕГРИП: ошибка сервиса (HTTP 503)"
    offer = {"product_code": "EXPRESS", "amount": 300_000, "term_months": 6}
    resp = a.call("POST", _bank(app, "/decision"), json={"action": "APPROVE", "offers": [offer]}, expect=409)
    assert "после проверки по ЕГРЮЛ, ФНС и БКИ" in resp["detail"]
    # запросить информацию можно и без сведений из источников
    # (отказ здесь не оформляем: он отправил бы следующие заявки компании к специалисту)
    a.call(
        "POST",
        _bank(app, "/decision"),
        json={"action": "REQUEST_INFO", "info_items": [{"type": "COMMENT", "label": "Подтвердите юридический адрес"}]},
    )


def test_bank_offer_limits_are_explained(login):
    app = _manual_review(login)
    a = login("analyst")
    a.call("POST", _bank(app, "/assign"))
    bad = {"product_code": "INVESTMENT", "amount": 500_000_000, "term_months": 60}
    resp = a.call("POST", _bank(app, "/decision"), json={"action": "APPROVE", "offers": [bad]}, expect=422)
    assert (
        resp["detail"]
        == "Сумма по продукту «Инвестиционный кредит» — от 1\u00a0000\u00a0000 до 100\u00a0000\u00a0000\u00a0₽"
    )
    rate = {"product_code": "INVESTMENT", "amount": 20_000_000, "term_months": 60, "rate": 49}
    resp = a.call("POST", _bank(app, "/decision"), json={"action": "APPROVE", "offers": [rate]}, expect=422)
    assert resp["detail"].startswith("Ставка по продукту «Инвестиционный кредит» — от ")
    assert ",0%" not in resp["detail"]


def test_settings_validation(login):
    from app.db import SessionLocal
    from app.models import Setting

    a, r = login("analyst"), login("risk")
    resp = a.call("PUT", "/api/v1/bank/settings/key_rate", json={"value": 15}, expect=403)
    assert resp["detail"] == "Менять параметры кредитной политики может только риск-менеджер"
    with SessionLocal() as db:
        saved = {k: (db.get(Setting, k).value if db.get(Setting, k) else None) for k in ("key_rate", "key_rate_note")}
    try:
        for bad in (10**400, True, "много", 0, 50, None, [1]):
            r.call("PUT", "/api/v1/bank/settings/key_rate", json={"value": bad}, expect=422)
        for bad in (None, "", "   ", "x" * 301, 5):
            r.call("PUT", "/api/v1/bank/settings/key_rate_note", json={"value": bad}, expect=422)
        assert r.call("PUT", "/api/v1/bank/settings/key_rate", json={"value": "15,5"})["value"] == 15.5
        # подпись к ставке обновляется вместе со ставкой, чтобы не остаться от прошлого значения
        note = next(s for s in r.call("GET", "/api/v1/bank/settings") if s["key"] == "key_rate_note")["value"]
        assert note.startswith("Установлена в банке ")
    finally:
        with SessionLocal() as db:
            for key, value in saved.items():
                row = db.get(Setting, key)
                if value is None:
                    if row is not None:
                        db.delete(row)
                else:
                    row.value = value
            db.commit()


# --------------------------------------------------------------------------- выдача и график


def test_failed_disbursement_waits_for_bank_retry(login):
    from app.db import SessionLocal
    from app.models import Loan

    c, a = login("smirnov"), login("analyst")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)
    assert app["status"] == "OFFER_READY"
    a.call("POST", "/api/v1/bank/integrations/faults", json={"service": "ABS", "mode": "down", "duration_seconds": 600})
    app = c.accept_and_sign(app)  # все попытки выдачи исчерпаны
    assert app["status"] == "DISBURSEMENT"
    assert app["status_view"]["now"] == "Выдача задерживается: учётная система банка не ответила вовремя."
    c.call("POST", _url(app, "/cancel"), expect=409)

    queue = a.call("GET", "/api/v1/bank/applications", params={"scope": "queue"})
    row = next(x for x in queue if x["id"] == app["id"])
    assert row["triggers"] == ["Выдача не прошла — повторите её, когда АБС снова доступна"]
    dossier = a.call("GET", _bank(app))
    assert dossier["permissions"]["can_retry_disbursement"] is True
    assert any(e["event_type"] == "DISBURSEMENT_FAILED" for e in dossier["events"])

    a.call("POST", "/api/v1/bank/integrations/faults", json={"service": "ABS", "mode": "ok"})
    a.call("POST", _bank(app, "/rerun"))
    a.call("POST", _bank(app, "/rerun"), expect=409)  # повтор уже в очереди
    drain()
    assert c.get(app["id"])["status"] == "ISSUED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Loan).where(Loan.application_id == app["id"])) == 1


def test_signed_schedule_is_the_loan_schedule(login):
    from app.db import SessionLocal
    from app.models import Offer

    c = login("smirnov")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)
    offer_id = app["offers"][0]["id"]
    c.call("POST", _url(app, f"/offers/{offer_id}/accept"))
    signed = c.call("GET", _url(app, "/contract"))["schedule"]
    ch = c.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONTRACT"})
    c.call("POST", _url(app, "/sign"), json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]})
    with SessionLocal() as db:
        offer = db.get(Offer, offer_id)
        assert offer.signed_at is not None
        # выдача задержалась на два дня (например, АБС была недоступна): график — от даты подписания
        db.execute(update(Offer).where(Offer.id == offer_id).values(signed_at=offer.signed_at - timedelta(days=2)))
        db.commit()
    drain()
    app = c.get(app["id"])
    loan = c.call("GET", f"/api/v1/loans/{app['loan']['id']}")
    contract = c.call("GET", _url(app, "/contract"))["schedule"]
    assert loan["schedule"] == contract
    assert [p["date"] for p in signed] != [p["date"] for p in contract]  # дата подписания сдвинута тестом
    first = datetime.fromisoformat(contract[0]["date"]).date()
    assert loan["first_payment_date"] == first.isoformat()


def test_overdraft_is_described_as_a_limit(login):
    u = login("morozov")
    # заявленная выручка не выше поступлений на счёт — подтверждать её не нужно
    app = u.prepare_and_submit("OVERDRAFT", "CASH_GAP", 300_000, 12, declared_revenue=7_000_000)
    assert app["status"] == "OFFER_READY", app["status"]
    app = u.accept_and_sign(app)
    assert app["status_view"]["title"] == "Овердрафт подключён"
    titles = [e["title"] for e in app["events"]]
    assert "Договор подписан, устанавливаем лимит овердрафта" in titles
    assert not any("перечисляем деньги" in t for t in titles)


# --------------------------------------------------------------------------- тексты и уведомления


def test_client_actions_are_neutral_and_do_not_notify(login):
    from app.db import SessionLocal
    from app.models import Notification, User

    owner = login("smirnov")
    app = owner.prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)
    with SessionLocal() as db:
        orlova = db.scalar(select(User).where(User.login == "orlova"))
        before = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == orlova.id))
    offer = app["offers"][0]
    owner.call("POST", _url(app, f"/offers/{offer['id']}/accept"))
    back = owner.call("POST", _url(app, "/signing/back"))
    assert back["events"][-1]["title"] == "Возврат к выбору условий"
    with SessionLocal() as db:
        after = db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == orlova.id))
    assert after == before  # «готово предложение» после возврата к выбору — не новое предложение банка
    # бухгалтер без права подписи видит, кто может принять предложение
    view = login("orlova").get(app["id"])["status_view"]
    assert "Передать на подпись" in view["next"] and "МСК" in view["next"]


def test_answer_to_bank_must_fit_product_terms(login):
    owner = login("smirnov")
    app = owner.prepare_and_submit("EXPRESS", "OTHER", 500_000, 6, declared_revenue=120_000_000)
    assert app["status"] == "NEEDS_INFO"
    owner.demo_doc(app["id"], "BANK_STATEMENT")
    resp = owner.call(
        "POST",
        _url(app, "/info-response"),
        json={"comment": "Выписка приложена", "fields": {"amount": 50_000_000}},
        expect=422,
    )
    assert resp["detail"]["problems"] == ["Сумма должна быть от 300\u00a0000 до 5\u00a0000\u00a0000\u00a0₽"]
    assert owner.get(app["id"])["amount"] == 500_000


def test_declining_an_expired_offer_closes_it_as_expired(login):
    from app.db import SessionLocal
    from app.models import Offer

    c = login("smirnov")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)
    with SessionLocal() as db:
        db.execute(
            update(Offer).where(Offer.application_id == app["id"]).values(valid_until=datetime(2000, 1, 1, tzinfo=UTC))
        )
        db.commit()
    resp = c.call("POST", _url(app, "/offers/decline"), expect=409)
    assert resp["detail"] == "Срок действия предложения уже истёк"
    assert c.get(app["id"])["status"] == "EXPIRED"


def test_overdue_review_says_so(login):
    from app.db import SessionLocal
    from app.models import Application

    app = _manual_review(login)
    with SessionLocal() as db:
        db.execute(
            update(Application)
            .where(Application.id == app["id"])
            .values(review_due_at=datetime(2000, 1, 1, tzinfo=UTC))
        )
        db.commit()
    view = login("lebedev").get(app["id"])["status_view"]
    assert view["next"].startswith("Рассмотрение занимает больше времени, чем обычно")


def test_unread_counter_counts_everything(login):
    from app.db import SessionLocal
    from app.models import Notification, User

    u = login("kuznetsova")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.login == "kuznetsova"))
        db.add_all([Notification(user_id=user.id, title=f"Уведомление {i}", body="Текст") for i in range(35)])
        db.commit()
    data = u.call("GET", "/api/v1/notifications")
    assert len(data["items"]) == 30 and data["unread"] >= 35
    u.call("POST", "/api/v1/notifications/read-all", expect=204)
    assert u.call("GET", "/api/v1/notifications")["unread"] == 0


# --------------------------------------------------------------------------- воркер


def test_worker_survives_application_change_during_external_call(login, monkeypatch):
    """Пока этап ждёт ответа внешней системы, клиент отзывает заявку: воркер не падает,
    ошибка этапа сохраняется на свежей версии заявки."""
    from app.db import SessionLocal
    from app.domain.enums import JobStage, JobStatus
    from app.integrations.client import IntegrationError
    from app.models import Application, PipelineJob
    from app.services import pipeline

    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    c.sign_consents(app["id"])
    c.submit(app["id"])

    def slow_enrichment(db, application, job):
        if application.status != "ENRICHMENT":
            return  # как настоящий этап: заявку уже отозвали
        application.stage_detail = "Запрашиваем кредитную историю в БКИ"
        db.commit()
        c.call("POST", _url(app, "/cancel"))  # параллельный запрос меняет версию заявки
        raise IntegrationError("BKI", "таймаут ответа")

    monkeypatch.setitem(pipeline.HANDLERS, JobStage.ENRICHMENT, slow_enrichment)
    drain()  # проверка полноты → сбор данных: воркер не должен упасть
    with SessionLocal() as db:
        jobs = db.scalars(
            select(PipelineJob).where(PipelineJob.application_id == app["id"]).order_by(PipelineJob.id)
        ).all()
        assert db.get(Application, app["id"]).status == "CANCELLED"
    assert all(j.status in (JobStatus.DONE, JobStatus.FAILED) for j in jobs)
    # у отозванной заявки не появляется «БКИ временно не отвечает»
    assert c.get(app["id"])["stage_detail"] is None


def test_bank_sees_that_client_waits_for_reply(login):
    c, a = login("smirnov"), login("analyst")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)

    def row() -> dict:
        rows = a.call("GET", "/api/v1/bank/applications", params={"scope": "all", "q": app["number"]})
        return rows[0]

    assert row()["awaiting_reply"] is False
    c.call("POST", _url(app, "/messages"), json={"text": "Можно ли досрочно погасить без комиссии?"})
    assert row()["awaiting_reply"] is True
    assert a.call("GET", _bank(app))["awaiting_reply"] is True
    a.call("POST", _bank(app, "/messages"), json={"text": "Да, в любой день"})
    assert row()["awaiting_reply"] is False


# --------------------------------------------------------------------------- второй раунд проверки


def test_specialist_decline_is_not_bypassed_by_a_new_application(login):
    c, a = login("nikitin"), login("analyst")
    app = c.prepare_and_submit(
        "WORKING_CAPITAL",
        "CONTRACT",
        8_000_000,
        18,
        docs=["CONTRACT_COPY", "FIN_STATEMENTS"],
    )
    assert app["status"] == "MANUAL_REVIEW", app["status"]
    a.call("POST", _bank(app, "/decision"), json={"action": "DECLINE", "reason": "Недостаточное обеспечение"})
    # конструктор не пересчитывает решение специалиста
    resp = c.call("POST", _url(app, "/simulation"), json={}, expect=409)
    assert resp["detail"] == "По заявке решение принял кредитный специалист — условия подбирает он"
    # новая заявка на меньшую сумму всё равно попадает к специалисту
    new = c.call("POST", _url(app, "/reapply"), json={"amount": 1_000_000})
    c.sign_consents(new["id"])
    c.submit(new["id"])
    drain()
    new = c.get(new["id"])
    assert new["status"] == "MANUAL_REVIEW", new["status"]
    triggers = a.call("GET", _bank(new))["triggers"]
    assert any(t.startswith(f"Кредитный специалист отказал по заявке № {app['number']}") for t in triggers)


def test_assigned_case_is_not_taken_over(login):
    a, r = login("analyst"), login("risk")
    app = _manual_review(login)
    a.call("POST", _bank(app, "/assign"))
    a.call("POST", _bank(app, "/assign"))  # повторно — без ошибки
    # риск-менеджер может забрать заявку; аналитик — нет
    r.call("POST", _bank(app, "/assign"))
    resp = a.call("POST", _bank(app, "/assign"), expect=409)
    assert resp["detail"].startswith("Заявку уже рассматривает ") and "риск-менеджер" in resp["detail"]
    other = login("smirnov").prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)
    resp = a.call("POST", _bank(other, "/assign"), expect=409)
    assert resp["detail"] == "Взять в работу можно заявку на ручном рассмотрении"


def test_parent_is_set_only_by_reapply_and_conditions_are_checked(login):
    c = login("smirnov")
    first = c.draft("EXPRESS", "OTHER", 300_000, 6)
    c.call("POST", _url(first, "/cancel"))
    body = {
        "company_id": c.company_id,
        "product_code": "EXPRESS",
        "purpose": "OTHER",
        "amount": 300_000,
        "term_months": 6,
        "parent_id": first["id"],
    }
    assert c.call("POST", "/api/v1/applications", json=body)["parent_id"] is None
    resp = c.call("POST", _url(first, "/reapply"), json={"amount": 0}, expect=422)
    assert resp["detail"]["problems"] == ["Сумма: должно быть не меньше 1"]
    resp = c.call("POST", _url(first, "/reapply"), json={"amount": 50_000}, expect=422)
    assert resp["detail"] == "Сумма по продукту «Экспресс-кредит» — от 300\u00a0000 до 5\u00a0000\u00a0000\u00a0₽"
    resp = c.call("POST", _url(first, "/reapply"), json={"term_months": 99}, expect=422)
    assert resp["detail"] == "Срок по продукту «Экспресс-кредит» — 3–36 мес."
    resp = c.call("PATCH", _url(c.draft("EXPRESS", "OTHER", 300_000, 6)), json={"amount": True}, expect=422)
    assert resp["detail"]["problems"] == ["Сумма: должно быть числом"]


def test_signer_does_not_ask_himself_and_consents_are_signed_once(login):
    c = login("smirnov")
    app = c.draft("EXPRESS", "OTHER", 300_000, 6)
    resp = c.call("POST", _url(app, "/request-signature"), expect=409)
    assert resp["detail"] == "У вас есть право подписи — подпишите сами"
    c.sign_consents(app["id"])
    ch = c.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONSENTS"})
    resp = c.call(
        "POST", _url(app, "/consents"), json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]}, expect=409
    )
    assert resp["detail"] == "Согласия уже подписаны"
    titles = [e["title"] for e in c.get(app["id"])["events"]]
    assert titles.count("Подписаны согласия на запрос в БКИ и обработку данных") == 1


def test_demo_users_are_disabled_outside_demo_mode(api, monkeypatch):
    from sqlalchemy import select
    from sqlalchemy import update as sa_update

    import app.main as main
    from app.db import SessionLocal
    from app.models import User
    from app.security import hash_password

    with SessionLocal() as db:
        # учётка с демо-логином, но своим паролем — это уже не демо-учётка: её не трогаем
        db.execute(sa_update(User).where(User.login == "nikitin").values(password_hash=hash_password("свой-пароль-1")))
        db.commit()
    try:
        monkeypatch.setattr(main.settings, "demo_mode", False)
        main._sync_demo_users()
        assert api.post("/api/v1/auth/login", json={"login": "morozov", "password": "demo1234"}).status_code == 401
        with SessionLocal() as db:
            assert db.scalar(select(User.is_active).where(User.login == "nikitin")) is True
        # обратно в демо-режим — демо-учётки снова работают
        monkeypatch.setattr(main.settings, "demo_mode", True)
        main._sync_demo_users()
        assert api.post("/api/v1/auth/login", json={"login": "morozov", "password": "demo1234"}).status_code == 200
    finally:
        with SessionLocal() as db:
            db.execute(sa_update(User).values(is_active=True))
            db.execute(sa_update(User).where(User.login == "nikitin").values(password_hash=hash_password("demo1234")))
            db.commit()


def test_no_chat_for_application_never_sent_to_bank(login):
    c = login("smirnov")
    draft = c.draft("EXPRESS", "OTHER", 300_000, 6)
    resp = c.call("POST", _url(draft, "/messages"), json={"text": "Вопрос по заявке"}, expect=409)
    assert resp["detail"] == "Переписка с банком откроется после отправки заявки"
    c.call("POST", _url(draft, "/cancel"))
    c.call("POST", _url(draft, "/messages"), json={"text": "Вопрос по заявке"}, expect=409)
