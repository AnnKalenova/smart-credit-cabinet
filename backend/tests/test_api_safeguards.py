"""Защитные проверки конвейера: сбой любого автоматического этапа передаёт заявку специалисту."""

import pytest
from sqlalchemy import select

from tests.conftest import drain


def _url(app: dict, suffix: str = "") -> str:
    return f"/api/v1/applications/{app['id']}{suffix}"


def _bank(app: dict, suffix: str = "") -> str:
    return f"/api/v1/bank/applications/{app['id']}{suffix}"


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
