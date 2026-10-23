"""Генератор Postman-коллекции со сквозными сценариями (docs/postman/).

Коллекция проходит те же исходы, что и e2e/api_scenarios.py: каждый запрос проверяет ответ,
нужные идентификаторы сохраняются в переменные, а ожидание обработки заявки сделано повтором
запроса через pm.execution.setNextRequest. Запуск: Postman → Run collection или
    npx newman run docs/postman/smart-credit-cabinet.postman_collection.json

Запуск генератора: python tools/build_postman_collection.py
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "postman" / "smart-credit-cabinet.postman_collection.json"
PROCESSING = ["SUBMITTED", "PRECHECK", "ENRICHMENT", "SCORING", "DECISION", "DISBURSEMENT"]


def script(lines: list[str]) -> list[dict]:
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": lines}}]


def request(
    name: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    token: str | None = "clientToken",
    tests: list[str] | None = None,
    expect: int = 200,
    query: list[tuple[str, str]] | None = None,
    description: str = "",
) -> dict:
    url_path = path.lstrip("/").split("/")
    raw = "{{baseUrl}}/" + path.lstrip("/")
    url: dict = {"raw": raw, "host": ["{{baseUrl}}"], "path": url_path}
    if query:
        url["query"] = [{"key": k, "value": v} for k, v in query]
        url["raw"] += "?" + "&".join(f"{k}={v}" for k, v in query)
    req: dict = {"method": method, "header": [], "url": url, "description": description}
    if body is not None:
        req["header"].append({"key": "Content-Type", "value": "application/json"})
        req["body"] = {"mode": "raw", "raw": json.dumps(body, ensure_ascii=False, indent=2)}
    req["auth"] = (
        {"type": "bearer", "bearer": [{"key": "token", "value": "{{" + token + "}}", "type": "string"}]}
        if token
        else {"type": "noauth"}
    )
    checks = [f"pm.test('HTTP {expect}', () => pm.response.to.have.status({expect}));"]
    return {"name": name, "event": script(checks + (tests or [])), "request": req}


def login(name: str, login_value: str, var: str = "clientToken", company: bool = True) -> dict:
    tests = [
        "const r = pm.response.json();",
        f"pm.collectionVariables.set('{var}', r.access_token);",
    ]
    item = request(
        f"Вход: {name}",
        "POST",
        "/api/v1/auth/login",
        body={"login": login_value, "password": "{{password}}"},
        token=None,
        tests=tests,
    )
    if not company:
        return item
    me = request(
        f"Компании пользователя ({name})",
        "GET",
        "/api/v1/auth/me",
        token=var,
        tests=["pm.collectionVariables.set('companyId', pm.response.json().companies[0].id);"],
    )
    return [item, me]


def poll(name: str, expected: str, token: str = "clientToken") -> dict:
    """Повторяет запрос, пока заявка в обработке; затем проверяет итоговый статус."""
    tests = [
        "const app = pm.response.json();",
        f"const processing = {json.dumps(PROCESSING)};",
        "const tries = Number(pm.collectionVariables.get('pollTries') || 0);",
        "if (processing.includes(app.status) && tries < 60) {",
        "  pm.collectionVariables.set('pollTries', tries + 1);",
        "  setTimeout(() => {}, 1500);",
        "  pm.execution.setNextRequest(pm.info.requestId);",
        "  return;",
        "}",
        "pm.collectionVariables.set('pollTries', 0);",
        f"pm.test('Статус {expected}', () => pm.expect(app.status).to.eql('{expected}'));",
        "if (app.offers && app.offers.length) pm.collectionVariables.set('offerId', app.offers[0].id);",
    ]
    return request(
        name,
        "GET",
        "/api/v1/applications/{{appId}}",
        token=token,
        tests=tests,
        description=f"Опрашивает заявку каждые 1,5 с, пока она в обработке. Ожидаемый итог: {expected}.",
    )


def create(product: str, purpose: str, amount: int, term: int) -> dict:
    return request(
        "Создать черновик",
        "POST",
        "/api/v1/applications",
        body={
            "company_id": "{{companyId}}",
            "product_code": product,
            "purpose": purpose,
            "amount": amount,
            "term_months": term,
        },
        expect=201,
        tests=["pm.collectionVariables.set('appId', pm.response.json().id);"],
    )


def consents() -> list[dict]:
    return [
        request(
            "Получить код подписи согласий",
            "POST",
            "/api/v1/applications/{{appId}}/sign-challenges",
            body={"purpose": "CONSENTS"},
            tests=[
                "const r = pm.response.json();",
                "pm.collectionVariables.set('challengeId', r.challenge_id);",
                "pm.collectionVariables.set('code', r.demo_code);",
            ],
            description="На демо-стенде код подтверждения возвращается в ответе (поле demo_code).",
        ),
        request(
            "Подписать согласия",
            "POST",
            "/api/v1/applications/{{appId}}/consents",
            body={"challenge_id": "{{challengeId}}", "code": "{{code}}"},
            tests=["pm.test('Два согласия', () => pm.expect(pm.response.json().consents.length).to.eql(2));"],
        ),
    ]


def submit() -> dict:
    return request("Отправить заявку", "POST", "/api/v1/applications/{{appId}}/submit")


def demo_doc(doc_type: str) -> dict:
    return request(
        f"Приложить пример документа {doc_type}",
        "POST",
        "/api/v1/applications/{{appId}}/documents/demo",
        query=[("doc_type", doc_type)],
        expect=201,
    )


def sign_and_issue() -> list[dict]:
    return [
        request("Принять первое предложение", "POST", "/api/v1/applications/{{appId}}/offers/{{offerId}}/accept"),
        request(
            "Проект договора и график",
            "GET",
            "/api/v1/applications/{{appId}}/contract",
            tests=["pm.test('Есть график', () => pm.expect(pm.response.json().schedule.length).to.be.above(0));"],
        ),
        request(
            "Получить код подписи договора",
            "POST",
            "/api/v1/applications/{{appId}}/sign-challenges",
            body={"purpose": "CONTRACT"},
            tests=[
                "const r = pm.response.json();",
                "pm.collectionVariables.set('challengeId', r.challenge_id);",
                "pm.collectionVariables.set('code', r.demo_code);",
            ],
        ),
        request(
            "Подписать договор",
            "POST",
            "/api/v1/applications/{{appId}}/sign",
            body={"challenge_id": "{{challengeId}}", "code": "{{code}}"},
        ),
        poll("Дождаться выдачи", "ISSUED"),
    ]


def folder(name: str, description: str, items: list) -> dict:
    flat: list[dict] = []
    for it in items:
        flat.extend(it if isinstance(it, list) else [it])
    # Стабильные ID: на них ссылается pm.execution.setNextRequest при опросе статуса
    for i, item in enumerate(flat):
        item["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}/{i}/{item['name']}"))
    return {"name": name, "description": description, "item": flat}


def build() -> dict:
    folders = [
        folder(
            "0. Служебное",
            "Проверка, что стенд поднят.",
            [
                request("Готовность API", "GET", "/health/ready", token=None),
                request("Демо-учётки", "GET", "/api/v1/auth/demo-accounts", token=None),
                request("OpenAPI", "GET", "/api/openapi.json", token=None),
            ],
        ),
        folder(
            "1. Автоматическое одобрение и выдача — ООО «Северный хлеб»",
            "Оборотный кредит 3 млн ₽ на 12 мес. Решение принимается автоматически, затем подпись и выдача.",
            [
                login("Смирнов (директор)", "smirnov"),
                request("Профиль компании и предварительный лимит", "GET", "/api/v1/companies/{{companyId}}"),
                request(
                    "Подбор продукта",
                    "POST",
                    "/api/v1/products/match",
                    body={
                        "company_id": "{{companyId}}",
                        "purpose": "WORKING_CAPITAL",
                        "amount": 3000000,
                        "term_months": 12,
                    },
                    tests=[
                        "pm.test('Есть варианты', () => pm.expect(pm.response.json().results.length).to.be.above(0));"
                    ],
                ),
                create("WORKING_CAPITAL", "WORKING_CAPITAL", 3_000_000, 12),
                request(
                    "Дополнить черновик",
                    "PATCH",
                    "/api/v1/applications/{{appId}}",
                    body={"purpose_details": "Закупка муки и сахара перед новогодним сезоном"},
                ),
                consents(),
                submit(),
                poll("Дождаться решения", "OFFER_READY"),
                sign_and_issue(),
                request("Кредиты компании", "GET", "/api/v1/loans", query=[("company_id", "{{companyId}}")]),
            ],
        ),
        folder(
            "2. Встречное предложение и конструктор условий — ИП Кузнецова",
            "Экспресс-кредит 1 млн ₽ на 12 мес. при высокой долговой нагрузке: банк предлагает больший срок или меньшую сумму. "
            "Конструктор узнаёт условия из предложения банка и подсказывает другие, при которых банк одобрит автоматически. "
            "Отказ от предложения и новая заявка на условиях подсказки — один вызов; новая заявка одобряется автоматически.",
            [
                login("Кузнецова", "kuznetsova"),
                create("EXPRESS", "EXPANSION", 1_000_000, 12),
                consents(),
                submit(),
                poll("Дождаться решения", "OFFER_READY"),
                request(
                    "Проверить встречные варианты",
                    "GET",
                    "/api/v1/applications/{{appId}}",
                    tests=[
                        "const app = pm.response.json();",
                        "pm.test('Встречное предложение', () => pm.expect(app.decision.outcome).to.eql('COUNTER_OFFER'));",
                        "pm.test('Два варианта', () => pm.expect(app.offers.length).to.eql(2));",
                        "const full = app.offers.find(o => o.amount === app.amount);",
                        "pm.test('Есть вариант на всю сумму', () => pm.expect(full).to.be.an('object'));",
                        "if (full) { pm.collectionVariables.set('bankOfferId', full.id); "
                        "pm.collectionVariables.set('bankOfferTerm', full.term_months); }",
                    ],
                ),
                request(
                    "Конструктор условий: текущие условия",
                    "POST",
                    "/api/v1/applications/{{appId}}/simulation",
                    body={},
                    tests=[
                        "const s = pm.response.json();",
                        "pm.test('Тот же исход, что у заявки', () => pm.expect(s.result.outcome).to.eql('COUNTER_OFFER'));",
                        "pm.test('Нагрузка выше 40%', () => pm.expect(s.result.payment_share).to.be.above(s.result.payment_share_cap));",
                        "const best = s.suggestions.find(x => x.result.outcome === 'APPROVE');",
                        "pm.test('Есть подсказка с автоматическим одобрением', () => pm.expect(best).to.be.an('object'));",
                        "pm.test('Подсказки не повторяют предложение банка', () => "
                        "s.suggestions.forEach(x => pm.expect(x.result.offer).to.eql(null)));",
                        "if (best) { pm.collectionVariables.set('simProduct', best.params.product_code); "
                        "pm.collectionVariables.set('simAmount', best.params.amount); "
                        "pm.collectionVariables.set('simTerm', best.params.term_months); }",
                    ],
                    description="Решение банка при текущих условиях заявки и подсказки, что изменить. Ничего не сохраняет.",
                ),
                request(
                    "Конструктор условий: условия из предложения банка",
                    "POST",
                    "/api/v1/applications/{{appId}}/simulation",
                    body={"amount": 1000000, "term_months": "{{bankOfferTerm}}", "with_suggestions": False},
                    tests=[
                        "const s = pm.response.json();",
                        "pm.test('Одобрим автоматически', () => pm.expect(s.result.outcome).to.eql('APPROVE'));",
                        "pm.test('Узнаёт вариант из предложения', () => "
                        "pm.expect(s.result.offer.id).to.eql(pm.collectionVariables.get('bankOfferId')));",
                    ],
                    description="Условия совпадают с вариантом банка: его выбирают в предложении, новая заявка не нужна.",
                ),
                request(
                    "Новая заявка на условиях подсказки вместо предложения",
                    "POST",
                    "/api/v1/applications/{{appId}}/reapply",
                    body={
                        "product_code": "{{simProduct}}",
                        "amount": "{{simAmount}}",
                        "term_months": "{{simTerm}}",
                        "replace_current": True,
                    },
                    expect=201,
                    tests=[
                        "const n = pm.response.json();",
                        "pm.test('Черновик на условиях подсказки', () => { pm.expect(n.status).to.eql('DRAFT'); "
                        "pm.expect(n.term_months).to.eql(Number(pm.collectionVariables.get('simTerm'))); });",
                        "pm.test('Связан с исходной заявкой', () => pm.expect(n.parent_id).to.eql(pm.collectionVariables.get('appId')));",
                        "pm.collectionVariables.set('parentId', pm.collectionVariables.get('appId'));",
                        "pm.collectionVariables.set('appId', n.id);",
                    ],
                    description="replace_current: отказ от действующего предложения и новая заявка — одной транзакцией.",
                ),
                request(
                    "Исходная заявка закрыта отказом от предложения",
                    "GET",
                    "/api/v1/applications/{{parentId}}",
                    tests=[
                        "const old = pm.response.json();",
                        "pm.test('Отказ клиента', () => pm.expect(old.status).to.eql('CLIENT_DECLINED'));",
                        "pm.test('Ссылка на новую заявку', () => "
                        "pm.expect(old.next_application.id).to.eql(pm.collectionVariables.get('appId')));",
                    ],
                ),
                consents(),
                submit(),
                poll("Дождаться решения по новой заявке", "OFFER_READY"),
                request(
                    "Проверить автоматическое одобрение",
                    "GET",
                    "/api/v1/applications/{{appId}}",
                    tests=[
                        "const app = pm.response.json();",
                        "pm.test('Одобрено, как показал конструктор', () => pm.expect(app.decision.outcome).to.eql('APPROVE'));",
                    ],
                ),
            ],
        ),
        folder(
            "3. Ручное рассмотрение — ООО «ТехноМаш НН» и аналитик",
            "Инвестиционный кредит 25 млн ₽ выше лимита автоматического решения: заявку одобряет аналитик.",
            [
                login("Лебедев (директор)", "lebedev"),
                create("INVESTMENT", "EQUIPMENT", 25_000_000, 60),
                request(
                    "Описать залог",
                    "PATCH",
                    "/api/v1/applications/{{appId}}",
                    body={"collateral_description": "Обрабатывающий центр с ЧПУ, стоимость 31 млн ₽"},
                ),
                demo_doc("SUPPLIER_INVOICE"),
                demo_doc("FIN_STATEMENTS"),
                consents(),
                submit(),
                poll("Дождаться ручного рассмотрения", "MANUAL_REVIEW"),
                login("Волкова (аналитик)", "analyst", var="bankToken", company=False),
                request(
                    "Очередь ручного рассмотрения",
                    "GET",
                    "/api/v1/bank/applications",
                    token="bankToken",
                    query=[("scope", "queue")],
                    tests=[
                        "pm.test('Заявка в очереди', () => pm.expect(pm.response.json().map(r => r.id)).to.include(pm.collectionVariables.get('appId')));"
                    ],
                ),
                request(
                    "Досье заявки",
                    "GET",
                    "/api/v1/bank/applications/{{appId}}",
                    token="bankToken",
                    tests=[
                        "const d = pm.response.json();",
                        "pm.test('Есть скоринг', () => pm.expect(d.scoring.score).to.be.above(0));",
                        "const rec = d.decisions[0].details.offers[0];",
                        "pm.collectionVariables.set('recProduct', rec.product_code);",
                        "pm.collectionVariables.set('recAmount', rec.amount);",
                        "pm.collectionVariables.set('recTerm', rec.term_months);",
                    ],
                ),
                request("Взять в работу", "POST", "/api/v1/bank/applications/{{appId}}/assign", token="bankToken"),
                request(
                    "Одобрить по рекомендации системы",
                    "POST",
                    "/api/v1/bank/applications/{{appId}}/decision",
                    token="bankToken",
                    body={
                        "action": "APPROVE",
                        "offers": [
                            {"product_code": "{{recProduct}}", "amount": "{{recAmount}}", "term_months": "{{recTerm}}"}
                        ],
                        "message_to_client": "Одобрили инвестиционный кредит, залог — приобретаемый станок.",
                        "comment_internal": "Финансовое положение устойчивое",
                    },
                ),
                poll("Клиент получил предложение", "OFFER_READY"),
            ],
        ),
        folder(
            "4. Запрос уточнений — ИП Морозов",
            "Овердрафт 350 тыс. ₽: заявленная выручка выше оборотов по счёту, банк просит выписку из другого банка.",
            [
                login("Морозов", "morozov"),
                create("OVERDRAFT", "CASH_GAP", 350_000, 12),
                request(
                    "Указать выручку", "PATCH", "/api/v1/applications/{{appId}}", body={"declared_revenue": 14400000}
                ),
                consents(),
                submit(),
                poll("Дождаться запроса", "NEEDS_INFO"),
                demo_doc("BANK_STATEMENT"),
                request(
                    "Ответить на запрос",
                    "POST",
                    "/api/v1/applications/{{appId}}/info-response",
                    body={
                        "comment": "Эквайринг кофейни подключён в другом банке, туда поступает около 1,2 млн ₽ в месяц"
                    },
                ),
                poll("Дождаться решения", "OFFER_READY"),
            ],
        ),
        folder(
            "5. Автоматический отказ — ООО «Вектор Логистик»",
            "Компания в стадии ликвидации — стоп-фактор.",
            [
                login("Фёдоров", "fedorov"),
                create("EXPRESS", "WORKING_CAPITAL", 2_000_000, 24),
                consents(),
                submit(),
                poll("Дождаться решения", "DECLINED"),
            ],
        ),
        folder(
            "6. Передача условий руководителю — ООО «Северный хлеб»",
            "Главный бухгалтер без права подписи подбирает условия в конструкторе и передаёт их руководителю: "
            "руководитель получает уведомление, а в заявке видит переданные условия.",
            [
                login("Смирнов (директор)", "smirnov"),
                create("EXPRESS", "OTHER", 500_000, 6),
                consents(),
                submit(),
                poll("Дождаться решения", "OFFER_READY"),
                login("Орлова (главный бухгалтер)", "orlova", var="employeeToken", company=False),
                request(
                    "Бухгалтер не заменяет заявку сам",
                    "POST",
                    "/api/v1/applications/{{appId}}/reapply",
                    token="employeeToken",
                    body={"amount": 400000, "term_months": 12, "owner_guarantee": True, "replace_current": True},
                    expect=403,
                ),
                request(
                    "Передать условия руководителю",
                    "POST",
                    "/api/v1/applications/{{appId}}/request-signature",
                    token="employeeToken",
                    body={"conditions": {"amount": 400000, "term_months": 12, "owner_guarantee": True}},
                    tests=[
                        "const r = pm.response.json();",
                        "pm.test('Условия сохранены в заявке', () => pm.expect(r.proposed_conditions.conditions.amount).to.eql(400000));",
                    ],
                ),
                request(
                    "Руководитель видит переданные условия",
                    "GET",
                    "/api/v1/applications/{{appId}}",
                    tests=[
                        "const pc = pm.response.json().proposed_conditions;",
                        "pm.test('Условия от бухгалтера', () => { pm.expect(pc.conditions.amount).to.eql(400000); "
                        "pm.expect(pc.conditions.term_months).to.eql(12); pm.expect(pc.conditions.owner_guarantee).to.eql(true); });",
                        "pm.test('Кто предложил', () => pm.expect(pc.by).to.eql('Орлова Мария Петровна'));",
                    ],
                ),
                request(
                    "Уведомление руководителю",
                    "GET",
                    "/api/v1/notifications",
                    tests=[
                        "const items = pm.response.json().items;",
                        "pm.test('Новые условия по заявке', () => pm.expect(items[0].title).to.include('Новые условия по заявке'));",
                    ],
                ),
            ],
        ),
        folder(
            "7. Банк: мониторинг, параметры, сбои",
            "Дашборд конвейера, изменение ключевой ставки риск-менеджером, моделирование сбоя БКИ.",
            [
                login("Белов (риск-менеджер)", "risk", var="bankToken", company=False),
                request("Дашборд", "GET", "/api/v1/bank/dashboard", token="bankToken"),
                request("Параметры политики", "GET", "/api/v1/bank/settings", token="bankToken"),
                request(
                    "Изменить ключевую ставку",
                    "PUT",
                    "/api/v1/bank/settings/key_rate",
                    token="bankToken",
                    body={"value": 14.0},
                ),
                request(
                    "Отключить БКИ на 30 с",
                    "POST",
                    "/api/v1/bank/integrations/faults",
                    token="bankToken",
                    body={"service": "BKI", "mode": "down", "duration_seconds": 30},
                ),
                request(
                    "Вернуть БКИ",
                    "POST",
                    "/api/v1/bank/integrations/faults",
                    token="bankToken",
                    body={"service": "BKI", "mode": "ok", "duration_seconds": 30},
                ),
            ],
        ),
    ]
    return {
        "info": {
            "_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "smart-credit-cabinet")),
            "name": "Умный кредитный кабинет предпринимателя",
            "description": (
                "Сквозные сценарии кредитного конвейера. Запускайте папки по порядку целиком (Run folder): "
                "переменные appId, offerId, challengeId заполняются автоматически. "
                "baseUrl по умолчанию — http://localhost:8080 (веб-шлюз nginx) или http://localhost:8000 (API напрямую)."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "baseUrl", "value": "http://localhost:8080"},
            {"key": "password", "value": "demo1234"},
            {"key": "clientToken", "value": ""},
            {"key": "bankToken", "value": ""},
            {"key": "employeeToken", "value": ""},
            {"key": "companyId", "value": ""},
            {"key": "appId", "value": ""},
            {"key": "offerId", "value": ""},
            {"key": "challengeId", "value": ""},
            {"key": "code", "value": ""},
            {"key": "pollTries", "value": "0"},
            {"key": "recProduct", "value": ""},
            {"key": "recAmount", "value": ""},
            {"key": "recTerm", "value": ""},
            {"key": "bankOfferId", "value": ""},
            {"key": "bankOfferTerm", "value": ""},
            {"key": "simProduct", "value": ""},
            {"key": "simAmount", "value": ""},
            {"key": "simTerm", "value": ""},
            {"key": "parentId", "value": ""},
        ],
        "item": folders,
    }


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Записано: {OUT}")


if __name__ == "__main__":
    main()
