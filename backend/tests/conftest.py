"""Общая инфраструктура тестов.

Интеграционные тесты работают с настоящим PostgreSQL (отдельная база *_test) и с симулятором
внешних систем, подключённым внутри процесса. Этапы конвейера выполняются синхронно:
вместо фонового воркера тест «прокачивает» очередь функциями воркера claim_job/process_job.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://credit:credit@127.0.0.1:5432/credit_test")
if not TEST_DB_URL.rsplit("/", 1)[-1].endswith("_test"):
    raise RuntimeError("Тесты пересоздают схему БД: имя тестовой базы должно оканчиваться на _test")

# Настройки должны быть заданы до импорта модулей приложения (движок БД создаётся при импорте)
os.environ.update(
    {
        "DATABASE_URL": TEST_DB_URL,
        "SYNTHETIC_DATA_PATH": str(REPO / "data" / "synthetic" / "companies.json"),
        "UPLOAD_DIR": tempfile.mkdtemp(prefix="credit-uploads-"),
        "INTEGRATIONS_BASE_URL": "http://integrations-mock",
        "PIPELINE_STAGE_DELAY_SECONDS": "0",
        "INTEGRATION_BACKOFF_BASE_SECONDS": "0",
        "INTEGRATION_MAX_ATTEMPTS": "3",
        "DEMO_MODE": "true",
        "SEED_ON_START": "false",
        "JWT_SECRET": "test-secret-0123456789abcdef0123456789",
        "LOG_LEVEL": "WARNING",
        "MOCK_LATENCY_MIN_MS": "0",
        "MOCK_LATENCY_MAX_MS": "0",
    }
)

PASSWORD = "demo1234"


def _load_mock_module():
    path = REPO / "integrations-mock" / "app" / "main.py"
    spec = importlib.util.spec_from_file_location("integrations_mock_main", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["integrations_mock_main"] = module
    spec.loader.exec_module(module)
    return module


MOCK = _load_mock_module()


@pytest.fixture(scope="session")
def database():
    """Чистая схема → миграции Alembic → синтетические данные."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DB_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")

    from app.db import SessionLocal
    from app.seed import seed

    with SessionLocal() as db:
        assert seed(db) is True
    yield


@pytest.fixture(scope="session")
def external_systems_in_process(database):
    """Адаптер внешних систем ходит в симулятор внутри процесса вместо сети."""
    import httpx
    from starlette.testclient import TestClient

    import app.integrations.client as integrations

    def client_factory(base_url=None, timeout=None, headers=None):  # noqa: ARG001
        return TestClient(MOCK.app, base_url="http://integrations-mock", headers=headers)

    fake_httpx = types.SimpleNamespace(
        Client=client_factory,
        TimeoutException=httpx.TimeoutException,
        HTTPError=httpx.HTTPError,
    )
    mp = pytest.MonkeyPatch()
    mp.setattr(integrations, "httpx", fake_httpx)
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def reset_faults():
    MOCK.FAULTS.clear()
    yield
    MOCK.FAULTS.clear()


@pytest.fixture(scope="session")
def api(external_systems_in_process):
    """API в процессе. Тянет за собой БД и симулятор — модульным тестам они не нужны."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


class ApiUser:
    """Клиент API от имени демо-пользователя."""

    def __init__(self, api, login: str, token: str):
        self.api = api
        self.login = login
        self.headers = {"Authorization": f"Bearer {token}"}
        self.me = self.call("GET", "/api/v1/auth/me")

    def call(self, method: str, url: str, expect=(200, 201, 204), **kwargs):
        resp = self.api.request(method, url, headers=self.headers, **kwargs)
        expected = (expect,) if isinstance(expect, int) else expect
        assert resp.status_code in expected, f"{method} {url} → {resp.status_code}: {resp.text[:500]}"
        if resp.content and resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp

    @property
    def company_id(self) -> str:
        return self.me["companies"][0]["id"]

    # --- типовые шаги клиентского пути

    def draft(self, product: str, purpose: str, amount: float, term: int, **fields) -> dict:
        app = self.call(
            "POST",
            "/api/v1/applications",
            json={
                "company_id": self.company_id,
                "product_code": product,
                "purpose": purpose,
                "amount": amount,
                "term_months": term,
            },
        )
        if fields:
            app = self.call("PATCH", f"/api/v1/applications/{app['id']}", json=fields)
        return app

    def demo_doc(self, app_id: str, doc_type: str) -> dict:
        return self.call("POST", f"/api/v1/applications/{app_id}/documents/demo", params={"doc_type": doc_type})

    def sign_consents(self, app_id: str) -> dict:
        ch = self.call("POST", f"/api/v1/applications/{app_id}/sign-challenges", json={"purpose": "CONSENTS"})
        return self.call(
            "POST",
            f"/api/v1/applications/{app_id}/consents",
            json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]},
        )

    def submit(self, app_id: str) -> dict:
        return self.call("POST", f"/api/v1/applications/{app_id}/submit")

    def get(self, app_id: str) -> dict:
        return self.call("GET", f"/api/v1/applications/{app_id}")

    def prepare_and_submit(self, product, purpose, amount, term, docs=(), **fields) -> dict:
        app = self.draft(product, purpose, amount, term, **fields)
        for d in docs:
            self.demo_doc(app["id"], d)
        self.sign_consents(app["id"])
        self.submit(app["id"])
        drain()
        return self.get(app["id"])

    def accept_and_sign(self, app: dict, offer_index: int = 0) -> dict:
        offer = app["offers"][offer_index]
        self.call("POST", f"/api/v1/applications/{app['id']}/offers/{offer['id']}/accept")
        ch = self.call("POST", f"/api/v1/applications/{app['id']}/sign-challenges", json={"purpose": "CONTRACT"})
        self.call(
            "POST",
            f"/api/v1/applications/{app['id']}/sign",
            json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]},
        )
        drain()
        return self.get(app["id"])


_TOKENS: dict[str, str] = {}


@pytest.fixture(scope="session")
def login(api):
    def _login(user: str) -> ApiUser:
        if user not in _TOKENS:
            resp = api.post("/api/v1/auth/login", json={"login": user, "password": PASSWORD})
            assert resp.status_code == 200, resp.text
            _TOKENS[user] = resp.json()["access_token"]
        return ApiUser(api, user, _TOKENS[user])

    return _login


def drain(max_steps: int = 200) -> int:
    """Синхронно выполняет все этапы конвейера в очереди, игнорируя задержки и паузы повторов."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.domain.enums import JobStatus
    from app.models import PipelineJob
    from app.worker.runner import claim_job, process_job

    processed = 0
    for _ in range(max_steps):
        with SessionLocal() as db:
            db.execute(
                update(PipelineJob)
                .where(PipelineJob.status == JobStatus.PENDING)
                .values(run_after=datetime(2000, 1, 1, tzinfo=UTC))
            )
            db.commit()
            job_id = claim_job(db)
        if job_id is None:
            return processed
        process_job(job_id)
        processed += 1
    raise AssertionError("Очередь конвейера не опустела")


@pytest.fixture
def run_pipeline():
    return drain
