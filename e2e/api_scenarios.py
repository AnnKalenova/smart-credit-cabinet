"""Сквозные (end-to-end) сценарии через API — прогоняют все исходы кредитного конвейера.

Запуск против поднятого стенда:
    python e2e/api_scenarios.py --base-url http://localhost:8080
Нужен только httpx (pip install httpx). Скрипт возвращает код 1, если хоть один сценарий не прошёл.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

PROCESSING = {"SUBMITTED", "PRECHECK", "ENRICHMENT", "SCORING", "DECISION", "DISBURSEMENT"}
PASSWORD = "demo1234"


class Client:
    def __init__(self, base_url: str, login: str):
        self.http = httpx.Client(base_url=base_url, timeout=30)
        r = self.http.post("/api/v1/auth/login", json={"login": login, "password": PASSWORD})
        r.raise_for_status()
        self.http.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        self.me = self.http.get("/api/v1/auth/me").json()

    @property
    def company_id(self) -> str:
        return self.me["companies"][0]["id"]

    def req(self, method: str, url: str, expect: int | tuple = (200, 201, 204), **kw):
        r = self.http.request(method, url, **kw)
        expected = (expect,) if isinstance(expect, int) else expect
        if r.status_code not in expected:
            raise AssertionError(f"{method} {url} → {r.status_code}: {r.text[:400]}")
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None

    def wait(self, app_id: str, timeout: float = 90, bank: bool = False) -> dict:
        url = f"/api/v1/bank/applications/{app_id}" if bank else f"/api/v1/applications/{app_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            app = self.req("GET", url)
            if app["status"] not in PROCESSING:
                return app
            time.sleep(0.7)
        raise AssertionError(f"заявка {app_id} не вышла из обработки за {timeout} с")


def prepare(c: Client, product: str, purpose: str, amount: float, term: int, docs: list[str] = (), **fields) -> dict:
    app = c.req(
        "POST",
        "/api/v1/applications",
        json={
            "company_id": c.company_id,
            "product_code": product,
            "purpose": purpose,
            "amount": amount,
            "term_months": term,
        },
    )
    if fields:
        app = c.req("PATCH", f"/api/v1/applications/{app['id']}", json=fields)
    for d in docs:
        c.req("POST", f"/api/v1/applications/{app['id']}/documents/demo", params={"doc_type": d})
    ch = c.req("POST", f"/api/v1/applications/{app['id']}/sign-challenges", json={"purpose": "CONSENTS"})
    c.req(
        "POST",
        f"/api/v1/applications/{app['id']}/consents",
        json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]},
    )
    return app


def submit_and_wait(c: Client, app_id: str) -> dict:
    c.req("POST", f"/api/v1/applications/{app_id}/submit")
    return c.wait(app_id)


def accept_sign_issue(c: Client, app: dict, offer_index: int = 0) -> dict:
    offer = app["offers"][offer_index]
    c.req("POST", f"/api/v1/applications/{app['id']}/offers/{offer['id']}/accept")
    contract = c.req("GET", f"/api/v1/applications/{app['id']}/contract")
    assert contract["schedule"], "в договоре нет графика платежей"
    ch = c.req("POST", f"/api/v1/applications/{app['id']}/sign-challenges", json={"purpose": "CONTRACT"})
    c.req(
        "POST",
        f"/api/v1/applications/{app['id']}/sign",
        json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]},
    )
    final = c.wait(app["id"])
    assert final["status"] == "ISSUED", f"ожидали ISSUED, получили {final['status']}"
    assert final["loan"] and final["loan"]["loan_account"], "нет ссудного счёта"
    return final


def pct(v: float) -> str:
    return f"{v:g}".replace(".", ",") + "%"


def money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


# --------------------------------------------------------------------------- сценарии


def scenario_auto_approve(base: str) -> str:
    c = Client(base, "smirnov")
    app = prepare(c, "WORKING_CAPITAL", "WORKING_CAPITAL", 3_000_000, 12, purpose_details="Закупка муки и сахара")
    app = submit_and_wait(c, app["id"])
    assert app["status"] == "OFFER_READY", app["status"]
    assert app["decision"]["outcome"] == "APPROVE", app["decision"]
    assert len(app["offers"]) == 1 and not app["offers"][0]["is_counter"]
    o = app["offers"][0]
    final = accept_sign_issue(c, app)
    return f"одобрено автоматически: {money(o['amount'])} ₽ / {o['term_months']} мес. / {o['rate']}% → выдан, счёт {final['loan']['loan_account']}"


def scenario_counter_offer(base: str) -> str:
    c = Client(base, "kuznetsova")
    app = prepare(c, "EXPRESS", "EXPANSION", 1_000_000, 12, purpose_details="Ремонт второго зала")
    app = submit_and_wait(c, app["id"])
    assert app["status"] == "OFFER_READY", app["status"]
    assert app["decision"]["outcome"] == "COUNTER_OFFER", app["decision"]
    variants = "; ".join(f"{o['label']}: {money(o['amount'])} ₽ / {o['term_months']} мес." for o in app["offers"])
    accept_sign_issue(c, app, 0)
    return f"встречное предложение ({variants}) → выбран первый вариант, выдан"


def scenario_conditions_builder(base: str) -> str:
    c = Client(base, "kuznetsova")
    app = prepare(c, "EXPRESS", "EXPANSION", 1_000_000, 12, purpose_details="Ремонт второго зала")
    app = submit_and_wait(c, app["id"])
    assert app["decision"]["outcome"] == "COUNTER_OFFER", app["decision"]
    sim = c.req("POST", f"/api/v1/applications/{app['id']}/simulation", json={})
    assert sim["result"]["outcome"] == "COUNTER_OFFER", sim["result"]
    # условия из предложения банка конструктор узнаёт: их выбирают без новой заявки,
    # а более длинный срок, чем в предложении, не подсказывает — он хуже готового варианта
    offer = app["offers"][0]
    same = c.req(
        "POST",
        f"/api/v1/applications/{app['id']}/simulation",
        json={"amount": offer["amount"], "term_months": offer["term_months"], "with_suggestions": False},
    )
    assert same["result"]["offer"]["id"] == offer["id"], same["result"]
    assert not any(s["code"] == "term" for s in sim["suggestions"]), sim["suggestions"]
    # срок из предложения и поручительство собственника — одобрение со ставкой ниже, чем в предложении
    best = {"amount": offer["amount"], "term_months": offer["term_months"], "owner_guarantee": True}
    chosen = c.req("POST", f"/api/v1/applications/{app['id']}/simulation", json={**best, "with_suggestions": False})[
        "result"
    ]
    assert chosen["outcome"] == "APPROVE" and chosen["offer"] is None, chosen
    assert chosen["rate"] < offer["rate"], (chosen["rate"], offer["rate"])
    # отказ от предложения и новая заявка — одним действием
    new = c.req(
        "POST",
        f"/api/v1/applications/{app['id']}/reapply",
        json={**best, "replace_current": True, "expected_status": "OFFER_READY"},
    )
    assert new["parent_id"] == app["id"] and new["owner_guarantee"] is True, new
    assert c.req("GET", f"/api/v1/applications/{app['id']}")["status"] == "CLIENT_DECLINED"
    ch = c.req("POST", f"/api/v1/applications/{new['id']}/sign-challenges", json={"purpose": "CONSENTS"})
    c.req(
        "POST",
        f"/api/v1/applications/{new['id']}/consents",
        json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]},
    )
    new = submit_and_wait(c, new["id"])
    # решение по новой заявке — то, которое показал конструктор
    assert new["decision"]["outcome"] == "APPROVE", new["decision"]
    assert new["offers"][0]["rate"] == chosen["rate"], (new["offers"][0]["rate"], chosen["rate"])
    return (
        f"конструктор: {money(best['amount'])} ₽ / {best['term_months']} мес. с поручительством → "
        f"{chosen['verdict'].lower()} под {pct(chosen['rate'])} вместо {pct(offer['rate'])}; "
        "новая заявка одобрена автоматически"
    )


def scenario_manual_approve(base: str) -> str:
    c = Client(base, "lebedev")
    app = prepare(
        c,
        "INVESTMENT",
        "EQUIPMENT",
        25_000_000,
        60,
        docs=["SUPPLIER_INVOICE", "FIN_STATEMENTS"],
        collateral_description="Обрабатывающий центр с ЧПУ, стоимость 31 млн ₽",
    )
    app = submit_and_wait(c, app["id"])
    assert app["status"] == "MANUAL_REVIEW", app["status"]
    analyst = Client(base, "analyst")
    queue = analyst.req("GET", "/api/v1/bank/applications", params={"scope": "queue"})
    assert any(r["id"] == app["id"] for r in queue), "заявки нет в очереди аналитика"
    dossier = analyst.req("GET", f"/api/v1/bank/applications/{app['id']}")
    rec = dossier["decisions"][0]["details"]["offers"][0]
    analyst.req("POST", f"/api/v1/bank/applications/{app['id']}/assign")
    analyst.req(
        "POST",
        f"/api/v1/bank/applications/{app['id']}/decision",
        json={
            "action": "APPROVE",
            "offers": [
                {"product_code": rec["product_code"], "amount": rec["amount"], "term_months": rec["term_months"]}
            ],
            "comment_internal": "Финансовое положение устойчивое, залог ликвидный",
            "message_to_client": "Одобрили инвестиционный кредит. Залог — приобретаемый станок.",
        },
    )
    app = c.wait(app["id"])
    assert app["status"] == "OFFER_READY", app["status"]
    accept_sign_issue(c, app)
    return f"ручное рассмотрение (рейтинг {dossier['scoring']['grade']}) → аналитик одобрил {money(rec['amount'])} ₽ → выдан"


def scenario_manual_risky(base: str) -> str:
    c = Client(base, "nikitin")
    app = prepare(
        c,
        "WORKING_CAPITAL",
        "CONTRACT",
        8_000_000,
        18,
        docs=["CONTRACT_COPY"],
        purpose_details="Муниципальный контракт",
    )
    app = submit_and_wait(c, app["id"])
    assert app["status"] == "MANUAL_REVIEW", app["status"]
    analyst = Client(base, "analyst")
    dossier = analyst.req("GET", f"/api/v1/bank/applications/{app['id']}")
    triggers = dossier["triggers"]
    analyst.req(
        "POST",
        f"/api/v1/bank/applications/{app['id']}/decision",
        json={
            "action": "REQUEST_INFO",
            "info_items": [
                {
                    "type": "DOCUMENT",
                    "doc_type": "OTHER",
                    "label": "Справка об отсутствии задолженности по налогам или график её погашения",
                },
                {"type": "COMMENT", "label": "Расскажите о причинах смены руководителя"},
            ],
            "message_to_client": "Нужна пара документов, чтобы принять решение",
        },
    )
    app = c.wait(app["id"])
    assert app["status"] == "NEEDS_INFO", app["status"]
    c.req("POST", f"/api/v1/applications/{app['id']}/documents/demo", params={"doc_type": "OTHER"})
    c.req(
        "POST",
        f"/api/v1/applications/{app['id']}/info-response",
        json={"comment": "Прежний директор ушёл на пенсию, долг погасим до конца месяца"},
    )
    app = c.wait(app["id"])
    assert app["status"] == "MANUAL_REVIEW", app["status"]
    analyst.req(
        "POST",
        f"/api/v1/bank/applications/{app['id']}/decision",
        json={
            "action": "APPROVE",
            "offers": [{"product_code": "WORKING_CAPITAL", "amount": 5_000_000, "term_months": 18}],
            "comment_internal": "Снижаем сумму из-за налогового долга",
            "message_to_client": "Готовы выдать 5 млн ₽ — после погашения налогового долга можно увеличить лимит",
        },
    )
    app = c.wait(app["id"])
    assert app["status"] == "OFFER_READY" and app["offers"][0]["is_counter"]
    return f"ручное рассмотрение ({len(triggers)} триггера) → запрос документов → ответ клиента → встречное предложение 5 млн ₽"


def scenario_auto_decline(base: str) -> str:
    c = Client(base, "fedorov")
    app = prepare(c, "EXPRESS", "WORKING_CAPITAL", 2_000_000, 24)
    app = submit_and_wait(c, app["id"])
    assert app["status"] == "DECLINED", app["status"]
    return "отказ: " + "; ".join(app["decision"]["reasons"])


def scenario_needs_info(base: str) -> str:
    c = Client(base, "morozov")
    app = prepare(c, "OVERDRAFT", "CASH_GAP", 350_000, 12, declared_revenue=14_400_000)
    app = submit_and_wait(c, app["id"])
    assert app["status"] == "NEEDS_INFO", app["status"]
    items = app["info_request"]["items"]
    for item in items:
        if item["type"] == "DOCUMENT":
            c.req("POST", f"/api/v1/applications/{app['id']}/documents/demo", params={"doc_type": item["doc_type"]})
    c.req(
        "POST",
        f"/api/v1/applications/{app['id']}/info-response",
        json={"comment": "Эквайринг кофейни подключён в другом банке, туда поступает около 1,2 млн ₽ в месяц"},
    )
    app = c.wait(app["id"])
    assert app["status"] == "OFFER_READY", app["status"]
    o = app["offers"][0]
    accept_sign_issue(c, app)
    return f"запрос выписки → ответ → {app['decision']['outcome_label'].lower()}: лимит {money(o['amount'])} ₽ → установлен"


def scenario_accountant(base: str) -> str:
    c = Client(base, "orlova")
    app = c.req(
        "POST",
        "/api/v1/applications",
        json={
            "company_id": c.company_id,
            "product_code": "EXPRESS",
            "purpose": "OTHER",
            "amount": 500_000,
            "term_months": 6,
        },
    )
    c.req("POST", f"/api/v1/applications/{app['id']}/submit", expect=403)
    c.req("POST", f"/api/v1/applications/{app['id']}/request-signature")
    director = Client(base, "smirnov")
    notes = director.req("GET", "/api/v1/notifications")
    assert any("подпись" in n["title"].lower() for n in notes["items"]), "директор не получил уведомление"
    return "бухгалтеру отправка запрещена (403), руководитель получил уведомление «Нужна ваша подпись»"


SCENARIOS = [
    ("Автоматическое одобрение", scenario_auto_approve),
    # конструктор — раньше встречного предложения: тот сценарий выдаёт кредит, и долговая нагрузка
    # ИП Кузнецовой после него уже не позволит одобрить новую заявку
    ("Конструктор условий → новая заявка", scenario_conditions_builder),
    ("Встречное предложение", scenario_counter_offer),
    ("Ручное рассмотрение → одобрение", scenario_manual_approve),
    ("Повышенный риск → запрос информации → встречное", scenario_manual_risky),
    ("Автоматический отказ", scenario_auto_decline),
    ("Запрос уточнений → одобрение", scenario_needs_info),
    ("Права подписи (бухгалтер/директор)", scenario_accountant),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    args = parser.parse_args()
    failed = 0
    for title, fn in SCENARIOS:
        started = time.time()
        try:
            result = fn(args.base_url)
            print(f"✅ {title} ({time.time() - started:.1f} с): {result}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"❌ {title}: {e}")
    print(f"\nИтого: {len(SCENARIOS) - failed} из {len(SCENARIOS)} сценариев прошли")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
