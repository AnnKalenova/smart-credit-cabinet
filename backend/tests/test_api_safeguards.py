"""Защитные проверки конвейера: неполный ответ внешней системы не принимается за благополучный; сбой любого
автоматического этапа передаёт заявку специалисту; несколько полученных одновременно предложений нельзя принять
сверх ограничения долговой нагрузки — ни по очереди, ни одновременно, а специалист не может одобрить вариант сверх
него."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from tests.conftest import drain


def _url(app: dict, suffix: str = "") -> str:
    return f"/api/v1/applications/{app['id']}{suffix}"


def _bank(app: dict, suffix: str = "") -> str:
    return f"/api/v1/bank/applications/{app['id']}{suffix}"


# --------------------------------------------------------------------------- неполные ответы источников


def test_empty_registry_answer_goes_to_specialist(login):
    a = login("analyst")
    a.call(
        "POST", "/api/v1/bank/integrations/faults", json={"service": "EGRUL", "mode": "empty", "duration_seconds": 60}
    )
    assert a.call("GET", "/api/v1/bank/integrations/faults")["faults"]["EGRUL"]["mode"] == "empty"

    c = login("smirnov")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 300_000, 6)
    # пустая выписка — не «компания действует»: ни одобрения, ни рейтинга
    assert app["status"] == "MANUAL_REVIEW" and app["offers"] == []
    assert app["events"][-1]["title"] == "Передали заявку специалисту: внешний источник прислал неполные сведения"

    dossier = a.call("GET", _bank(app))
    assert dossier["scoring"] is None and "EGRUL" not in dossier["sources"]
    reason = dossier["decisions"][0]["reasons"][0]
    assert reason["code"] == "integration_unavailable"
    assert reason["text"] == "ЕГРЮЛ/ЕГРИП — пустой ответ: нет ни одного обязательного поля"
    calls = [x for x in dossier["integration_calls"] if x["service"] == "EGRUL"]
    assert len(calls) == 3 and all(x["status"] == "INVALID" and x["http_status"] == 200 for x in calls)
    assert calls[0]["error"] == "пустой ответ: нет ни одного обязательного поля"
    offer = {"product_code": "EXPRESS", "amount": 300_000, "term_months": 6}
    a.call("POST", _bank(app, "/decision"), json={"action": "APPROVE", "offers": [offer]}, expect=409)

    a.call("POST", "/api/v1/bank/integrations/faults", json={"service": "EGRUL", "mode": "ok"})
    a.call("POST", _bank(app, "/rerun"))
    drain()
    assert c.get(app["id"])["status"] == "OFFER_READY"


def test_empty_credit_history_answer_is_retried_not_trusted(login):
    a = login("analyst")
    a.call("POST", "/api/v1/bank/integrations/faults", json={"service": "BKI", "mode": "empty", "duration_seconds": 60})
    app = login("kuznetsova").prepare_and_submit("EXPRESS", "EXPANSION", 500_000, 12)
    assert app["status"] == "MANUAL_REVIEW"
    dossier = a.call("GET", _bank(app))
    assert "BKI" not in dossier["sources"] and {"EGRUL", "FNS"} <= set(dossier["sources"])
    assert dossier["decisions"][0]["reasons"][0]["text"].startswith("Бюро кредитных историй — пустой ответ")


# --------------------------------------------------------------------------- сбой автоматического этапа


@pytest.mark.parametrize(
    "target,stage,restart",
    [
        ("app.domain.checks.precheck", "PRECHECK", "PRECHECK"),
        # источник ответил, а упало сохранение сведений — это не «источник недоступен»
        ("app.services.pipeline._upsert_enrichment", "ENRICHMENT", "ENRICHMENT"),
        ("app.services.pipeline.score", "SCORING", "ENRICHMENT"),
        ("app.services.pipeline.evaluate", "DECISION", "ENRICHMENT"),
    ],
)
def test_stage_failure_goes_to_specialist_and_recovers_after_rerun(login, monkeypatch, target, stage, restart):
    from app.db import SessionLocal
    from app.models import PipelineJob

    def broken(*args, **kwargs):
        raise ZeroDivisionError("деление на ноль в расчёте")

    monkeypatch.setattr(target, broken)
    c = login("smirnov")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 350_000, 6)
    # не «зависла» на этапе: специалист видит заявку в очереди, срок решения назначен
    assert app["status"] == "MANUAL_REVIEW" and app["stage_detail"] is None and app["review_due_at"]
    assert app["events"][-1]["title"] == "Передали заявку специалисту: автоматическая проверка не завершилась"
    with SessionLocal() as db:
        job = db.scalar(select(PipelineJob).where(PipelineJob.application_id == app["id"], PipelineJob.stage == stage))
        assert job.status == "FAILED" and job.attempts == job.max_attempts == 3

    a = login("analyst")
    assert any(r["id"] == app["id"] for r in a.call("GET", "/api/v1/bank/applications", params={"scope": "queue"}))
    dossier = a.call("GET", _bank(app))
    reason = dossier["decisions"][0]["reasons"][0]
    assert reason["code"] == "technical_failure" and "деление на ноль в расчёте" in reason["text"]
    assert dossier["permissions"]["can_rerun"]
    # конструктор условий не повторяет упавший расчёт: у клиента он скрыт (409), а не падает с ошибкой
    c.call("POST", _url(app, "/simulation"), json={}, expect=409)

    monkeypatch.undo()  # ошибку исправили
    a.call("POST", _bank(app, "/rerun"))
    assert c.get(app["id"])["status"] == restart  # после сбоя проверки полноты она не пропускается
    drain()
    assert c.get(app["id"])["status"] == "OFFER_READY"


def test_application_stuck_before_handlers_existed_goes_to_specialist(login, monkeypatch):
    """Сбой случился до того, как у этапа появился обработчик: воркер-уборщик всё равно передаёт заявку специалисту."""
    import app.services.pipeline as pipeline
    from app.worker.runner import housekeeping

    def broken(*args, **kwargs):
        raise ZeroDivisionError("сбой в модели")

    monkeypatch.setattr(pipeline, "score", broken)
    monkeypatch.setitem(pipeline.FAILURE_HANDLERS, "SCORING", lambda db, app, error: None)  # как было раньше
    c = login("smirnov")
    app = c.prepare_and_submit("EXPRESS", "OTHER", 320_000, 6)
    assert app["status"] == "SCORING"  # зависла: попытки кончились, а обработчика не было
    a = login("analyst")
    a.call("POST", _bank(app, "/rerun"), expect=409)

    monkeypatch.undo()
    housekeeping()
    app = c.get(app["id"])
    assert app["status"] == "MANUAL_REVIEW" and app["review_due_at"]
    reason = a.call("GET", _bank(app))["decisions"][0]["reasons"][0]
    assert reason["code"] == "technical_failure" and "сбой в модели" in reason["text"]
    housekeeping()  # повторный проход ничего не меняет
    assert len(a.call("GET", _bank(app))["decisions"]) == 1
    a.call("POST", _bank(app, "/rerun"))
    drain()
    assert c.get(app["id"])["status"] == "OFFER_READY"


# --------------------------------------------------------------------------- долговая нагрузка


@pytest.fixture
def close_new_loans():
    """Кредиты, выданные в тесте, после него закрываются: остальные тесты считают нагрузку компаний без них."""
    from app.db import SessionLocal
    from app.models import Application, Loan

    started = datetime.now(UTC)
    yield
    with SessionLocal() as db:
        db.execute(update(Loan).where(Loan.created_at >= started).values(status="CLOSED"))
        db.execute(
            update(Application)
            .where(Application.created_at >= started, Application.status.in_(["SIGNING", "DISBURSEMENT"]))
            .values(status="CANCELLED")
        )
        db.commit()


def _sign(u, app: dict) -> dict:
    ch = u.call("POST", _url(app, "/sign-challenges"), json={"purpose": "CONTRACT"})
    u.call("POST", _url(app, "/sign"), json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]})
    drain()
    return u.get(app["id"])


def test_offers_cannot_be_accepted_beyond_debt_load_cap(api, login, monkeypatch, close_new_loans):
    import app.services.assessment as assessment

    u = login("smirnov")
    a = login("analyst")
    first = u.prepare_and_submit("WORKING_CAPITAL", "WORKING_CAPITAL", 6_000_000, 12)
    assert first["status"] == "OFFER_READY" and first["offers"][0]["unavailable_reason"] is None
    details = a.call("GET", _bank(first))["decisions"][0]["details"]
    income, existing = details["effective_monthly_turnover"], details["existing_monthly_payments"]
    payment = first["offers"][0]["monthly_payment"]
    fit = int((0.4 * income - existing) // payment)  # столько таких кредитов помещается в 40% поступлений
    assert 1 <= fit <= 6

    # все предложения получены до того, как принято хотя бы одно: каждое по отдельности укладывается в 40%
    apps = [first] + [u.prepare_and_submit("WORKING_CAPITAL", "WORKING_CAPITAL", 6_000_000, 12) for _ in range(fit)]
    assert all(x["status"] == "OFFER_READY" and x["offers"][0]["monthly_payment"] == payment for x in apps)

    # по очереди: выдаём кредиты, пока остаётся место ровно под один
    for x in apps[: fit - 1]:
        assert u.accept_and_sign(u.get(x["id"]))["status"] == "ISSUED"

    # одновременно: два последних предложения вместе не помещаются — принимается только одно
    original = assessment.load_room

    def slow_load_room(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(0.3)  # оба запроса посчитали нагрузку раньше, чем первый сохранил принятие
        return result

    monkeypatch.setattr(assessment, "load_room", slow_load_room)
    pair = apps[fit - 1 :]

    def accept(x: dict) -> int:
        return api.post(_url(x, f"/offers/{x['offers'][0]['id']}/accept"), headers=u.headers).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(accept, pair))
    monkeypatch.undo()
    assert sorted(codes) == [200, 409]
    won, lost = (pair[0], pair[1]) if codes[0] == 200 else (pair[1], pair[0])
    assert _sign(u, won)["status"] == "ISSUED"

    # оставшееся предложение честно помечено и не принимается
    lost = u.get(lost["id"])
    reason = lost["offers"][0]["unavailable_reason"]
    assert lost["status"] == "OFFER_READY" and "допустимо не больше 40%" in reason
    resp = u.call("POST", _url(lost, f"/offers/{lost['offers'][0]['id']}/accept"), expect=409)
    assert resp["detail"] == reason

    # конструктор не предлагает выбрать этот вариант: он считает условия с учётом выданных кредитов
    sim = u.call("POST", _url(lost, "/simulation"), json={"with_suggestions": False})["result"]
    assert sim["offer"] is None and sim["outcome"] != "APPROVE"

    # итог: платежи по всем кредитам компании — не больше 40% поступлений
    issued = sum(x["offers"][0]["monthly_payment"] for x in (u.get(y["id"]) for y in apps) if x["status"] == "ISSUED")
    assert issued == pytest.approx(fit * payment)
    assert existing + issued <= 0.4 * income + 1


def test_analyst_cannot_approve_beyond_debt_load_cap(login, close_new_loans):
    """Рекомендация по второй заявке рассчитана до того, как выдан кредит по первой: специалист её не отправит."""
    u = login("smirnov")
    a = login("analyst")
    first, second = (
        u.prepare_and_submit("WORKING_CAPITAL", "WORKING_CAPITAL", 12_000_000, 12, docs=["FIN_STATEMENTS"])
        for _ in range(2)
    )
    assert first["status"] == second["status"] == "MANUAL_REVIEW"  # сумма выше лимита автоматического решения
    offer = {"product_code": "WORKING_CAPITAL", "amount": 12_000_000, "term_months": 12}
    a.call("POST", _bank(first, "/decision"), json={"action": "APPROVE", "offers": [offer]})
    assert u.accept_and_sign(u.get(first["id"]))["status"] == "ISSUED"

    dossier = a.call("GET", _bank(second))
    load = dossier["load"]
    assert load["cap"] == 0.4 and 0 < load["room"] < dossier["decisions"][0]["details"]["offers"][0]["monthly_payment"]
    resp = a.call("POST", _bank(second, "/decision"), json={"action": "APPROVE", "offers": [offer]}, expect=422)
    assert resp["detail"].startswith("Вариант 1: платёж") and "допустимо не больше 40%" in resp["detail"]
    smaller = {**offer, "amount": 500_000}  # минимальная сумма продукта — помещается в оставшееся место
    a.call("POST", _bank(second, "/decision"), json={"action": "APPROVE", "offers": [smaller]})
    assert u.accept_and_sign(u.get(second["id"]))["status"] == "ISSUED"


def test_load_refusal_does_not_claim_new_loans_when_unknown():
    from app.services.assessment import LoadRoom

    known = LoadRoom(existing=1_500_000, income=4_000_000)
    assert known.refusal(400_000).startswith("После решения банка у компании появились новые кредиты")
    # решение без сохранённой нагрузки (принятое до проверки): почему не помещается — без догадок о новых кредитах
    unknown = LoadRoom(existing=1_500_000, income=4_000_000, new_obligations=False)
    text = unknown.refusal(400_000).replace("\xa0", " ")  # разряды — неразрывным пробелом
    assert text.startswith("Этот вариант не помещается в допустимую долговую нагрузку: вместе с ним платежи")
    assert "47,5%" in text and "до 100 000 ₽ в месяц" in text
    assert (
        LoadRoom(existing=1_700_000, income=4_000_000)
        .refusal(400_000)
        .endswith(
            "Свободного места под новый платёж сейчас нет. Подберите условия в конструкторе: он учитывает все действующие кредиты"
        )
    )
