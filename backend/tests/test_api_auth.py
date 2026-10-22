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
