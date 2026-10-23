"""Аутентификация, роли и изоляция данных между компаниями."""

CLIENT_LOGINS = {"smirnov", "orlova", "kuznetsova", "lebedev", "nikitin", "fedorov", "morozov"}


def test_login_and_me(login):
    u = login("smirnov")
    assert u.me["user"]["role"] == "CLIENT"
    company = u.me["companies"][0]
    assert company["short_name"] == "ООО «Северный хлеб»"
    assert company["can_sign"] is True
    assert login("orlova").me["companies"][0]["can_sign"] is False


def test_login_rejects_bad_credentials(api):
    assert api.post("/api/v1/auth/login", json={"login": "smirnov", "password": "wrong"}).status_code == 401
    assert api.post("/api/v1/auth/login", json={"login": "nobody", "password": "demo1234"}).status_code == 401


def test_requests_without_valid_token_are_rejected(api):
    assert api.get("/api/v1/auth/me").status_code == 401
    assert api.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_demo_accounts_cover_all_roles(api):
    accounts = api.get("/api/v1/auth/demo-accounts").json()
    logins = {a["login"] for a in accounts}
    assert CLIENT_LOGINS | {"analyst", "risk"} <= logins
    assert all(a["password"] == "demo1234" and a["scenario"] for a in accounts)


def test_role_guards(login):
    client, analyst, risk = login("smirnov"), login("analyst"), login("risk")
    client.call("GET", "/api/v1/bank/applications", expect=403)
    client.call("GET", "/api/v1/bank/dashboard", expect=403)
    analyst.call("GET", "/api/v1/applications", params={"company_id": client.company_id}, expect=403)
    analyst.call("PUT", "/api/v1/bank/settings/key_rate", json={"value": 15}, expect=403)
    risk.call("GET", "/api/v1/bank/settings")


def test_client_cannot_see_other_company_data(login):
    owner, stranger = login("smirnov"), login("kuznetsova")
    app = owner.draft("EXPRESS", "OTHER", 500_000, 6)
    stranger.call("GET", f"/api/v1/applications/{app['id']}", expect=404)
    stranger.call("PATCH", f"/api/v1/applications/{app['id']}", json={"amount": 600_000}, expect=404)
    stranger.call("GET", "/api/v1/applications", params={"company_id": owner.company_id}, expect=404)
    stranger.call("GET", f"/api/v1/companies/{owner.company_id}", expect=404)
    stranger.call(
        "POST",
        "/api/v1/products/match",
        json={"company_id": owner.company_id, "purpose": "OTHER", "amount": 1_000_000, "term_months": 12},
        expect=404,
    )
    # банк не видит неотправленные черновики
    login("analyst").call("GET", f"/api/v1/bank/applications/{app['id']}", expect=404)


def test_login_is_case_insensitive(api):
    assert api.post("/api/v1/auth/login", json={"login": " Smirnov ", "password": "demo1234"}).status_code == 200


def test_login_is_locked_after_repeated_failures(api, login):
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import LoginAttempt

    login("fedorov")  # токен кэшируется для остальных тестов
    try:
        for _ in range(10):
            assert api.post("/api/v1/auth/login", json={"login": "fedorov", "password": "wrong"}).status_code == 401
        locked = api.post("/api/v1/auth/login", json={"login": "Fedorov", "password": "demo1234"})
        assert locked.status_code == 429 and "10 минут" in locked.json()["detail"]
        # блокировка — по логину: другие пользователи входят как обычно
        assert api.post("/api/v1/auth/login", json={"login": "morozov", "password": "demo1234"}).status_code == 200
    finally:
        with SessionLocal() as db:
            db.execute(delete(LoginAttempt).where(LoginAttempt.login == "fedorov"))
            db.commit()
    assert api.post("/api/v1/auth/login", json={"login": "fedorov", "password": "demo1234"}).status_code == 200


def test_successful_login_resets_failure_counter(api):
    for _ in range(2):
        for _ in range(9):
            assert api.post("/api/v1/auth/login", json={"login": "risk", "password": "wrong"}).status_code == 401
        assert api.post("/api/v1/auth/login", json={"login": "risk", "password": "demo1234"}).status_code == 200


def test_validation_errors_are_explained_in_russian(login):
    u = login("smirnov")
    body = {"company_id": u.company_id, "product_code": "EXPRESS", "purpose": "OTHER", "term_months": 6}
    resp = u.call("POST", "/api/v1/applications", json={**body, "amount": 1e30}, expect=422)
    assert resp["detail"]["message"] == "Проверьте введённые данные"
    assert resp["detail"]["problems"] == ["Сумма: должно быть не больше 10 000 000 000"]
    resp = u.call("POST", "/api/v1/applications", json={**body, "amount": "много"}, expect=422)
    assert resp["detail"]["problems"] == ["Сумма: должно быть числом"]

    draft = u.draft("EXPRESS", "OTHER", 500_000, 6)
    resp = u.call("PATCH", f"/api/v1/applications/{draft['id']}", json={"amount": None}, expect=422)
    assert resp["detail"]["problems"] == ["Сумма: нельзя оставить пустым"]
    resp = u.call("PATCH", f"/api/v1/applications/{draft['id']}", json={"contact_phone": "1" * 40}, expect=422)
    assert resp["detail"]["problems"] == ["Телефон: слишком длинное значение (не больше 32 символов)"]
    assert u.get(draft["id"])["amount"] == 500_000


def test_parallel_wrong_passwords_do_not_bypass_lockout(api):
    from concurrent.futures import ThreadPoolExecutor

    def attempt(_):
        return api.post("/api/v1/auth/login", json={"login": "parallel-guess", "password": "wrong"}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(attempt, range(24)))
    # попытки по одному логину проверяются по очереди: пароль проверен ровно 10 раз
    assert codes.count(401) == 10 and codes.count(429) == 14


def test_unusual_login_does_not_break_login(api):
    # в нижнем регистре «İ» превращается в два символа: строка становится длиннее колонки
    resp = api.post("/api/v1/auth/login", json={"login": "İ" * 40, "password": "x"})
    assert resp.status_code == 401
