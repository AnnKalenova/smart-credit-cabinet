"""Нагрузочная проверка конвейера: предприниматели одновременно подают заявки (Locust).

Каждый виртуальный пользователь — предприниматель одной из демо-компаний: создаёт черновик, подписывает
согласия кодом, отправляет заявку и опрашивает её, как это делает кабинет, пока банк не примет решение.
Измеряется главное для клиента и банка: время от отправки заявки до решения (строки «конвейер» в отчёте)
и время ответа API под этой нагрузкой. Заявки не принимаются и не выдаются, поэтому долговая нагрузка
демо-компаний не растёт и прогон можно повторять на том же стенде.

    pip install locust
    locust -f loadtest/locustfile.py --headless --host http://localhost:8080 -u 50 -r 50 --run-time 10m \
        --csv loadtest/out/50x1 --only-summary

  -u — сколько предпринимателей подают заявки одновременно, -r — сколько из них начинают в секунду.
  Каждый пользователь подаёт APPLICATIONS_PER_USER заявок (по умолчанию 1) и завершает работу; прогон
  заканчивается, когда решения получены по всем заявкам, или по --run-time.
  METRICS_URL=http://localhost:8000/metrics — заодно записывать глубину очереди этапов (порт API открыт
  только на самом стенде). Итоги прогона — python loadtest/summarize.py loadtest/out/50x1.

Перед прогоном с другим числом воркеров: docker compose up -d --scale worker=3.
"""

from __future__ import annotations

import itertools
import logging
import os
import re
import threading
import time
import urllib.request

import gevent
from locust import HttpUser, between, events, task
from locust.exception import StopUser
from locust.runners import MasterRunner

PASSWORD = "demo1234"
PROCESSING = {"SUBMITTED", "PRECHECK", "ENRICHMENT", "SCORING", "DECISION"}
APPLICATIONS_PER_USER = int(os.environ.get("APPLICATIONS_PER_USER", "1"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "1.0"))  # кабинет опрашивает заявку в обработке раз в 1,2 с
METRICS_URL = os.environ.get("METRICS_URL", "")

# Демо-сценарии без ручных действий банка: каждый проходит все автоматические этапы конвейера
SCENARIOS = [
    {"login": "smirnov", "product": "WORKING_CAPITAL", "purpose": "WORKING_CAPITAL", "amount": 3_000_000, "term": 12},
    {"login": "kuznetsova", "product": "EXPRESS", "purpose": "EXPANSION", "amount": 1_000_000, "term": 12},
    {
        "login": "lebedev",
        "product": "INVESTMENT",
        "purpose": "EQUIPMENT",
        "amount": 25_000_000,
        "term": 60,
        "docs": ["SUPPLIER_INVOICE", "FIN_STATEMENTS"],
        "fields": {"collateral_description": "Обрабатывающий центр с ЧПУ"},
    },
    {"login": "fedorov", "product": "EXPRESS", "purpose": "WORKING_CAPITAL", "amount": 2_000_000, "term": 24},
    {
        "login": "morozov",
        "product": "OVERDRAFT",
        "purpose": "CASH_GAP",
        "amount": 350_000,
        "term": 12,
        "fields": {"declared_revenue": 14_400_000},
    },
]

_tokens: dict[str, tuple[str, str]] = {}  # login → (token, company_id)
_next_scenario = itertools.cycle(SCENARIOS)
_lock = threading.Lock()
queue_samples: list[tuple[float, int, int]] = []  # (момент, PENDING, RUNNING)


def _login_all(host: str) -> None:
    """Один вход на учётку за весь прогон: веб-шлюз пропускает не больше 20 входов в минуту с адреса."""
    import requests  # ставится вместе с Locust

    with requests.Session() as c:
        for s in SCENARIOS:
            if s["login"] in _tokens:
                continue
            r = c.post(f"{host}/api/v1/auth/login", json={"login": s["login"], "password": PASSWORD}, timeout=30)
            r.raise_for_status()
            token = r.json()["access_token"]
            me = c.get(f"{host}/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}, timeout=30)
            me.raise_for_status()
            _tokens[s["login"]] = (token, me.json()["companies"][0]["id"])


def _sample_queue(environment) -> None:
    pattern = re.compile(r'pipeline_queue_depth\{status="(PENDING|RUNNING)"\} ([0-9.]+)')
    while environment.runner and environment.runner.state not in ("stopped", "stopping"):
        try:
            with urllib.request.urlopen(METRICS_URL, timeout=3) as resp:  # noqa: S310 — адрес задаёт запускающий
                found = dict(pattern.findall(resp.read().decode()))
            queue_samples.append(
                (time.time(), int(float(found.get("PENDING", 0))), int(float(found.get("RUNNING", 0))))
            )
        except Exception:  # noqa: BLE001 — метрики необязательны
            pass
        gevent.sleep(1)


def _quit_when_done(environment) -> None:
    """Прогон завершается, когда все пользователи подали свои заявки и дождались решений."""
    runner = environment.runner
    while runner.state not in ("stopped", "stopping"):
        gevent.sleep(1)
        if runner.state == "running" and runner.user_count == 0:
            gevent.sleep(3)  # файлы --csv пишутся раз в секунду: последние решения должны в них попасть
            runner.quit()
            return


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    if isinstance(environment.runner, MasterRunner):
        return
    gevent.spawn(_quit_when_done, environment)
    try:
        _login_all(environment.host)
    except Exception as e:  # noqa: BLE001 — без входа прогон бессмыслен: завершаем сразу, а не висим до --run-time
        logging.getLogger("loadtest").error("не удалось войти на стенд %s: %s", environment.host, e)
        environment.process_exit_code = 1
        environment.runner.quit()
        return
    if METRICS_URL:
        gevent.spawn(_sample_queue, environment)


@events.quitting.add_listener
def on_quitting(environment, **kwargs):
    if METRICS_URL and queue_samples and environment.parsed_options and environment.parsed_options.csv_prefix:
        path = f"{environment.parsed_options.csv_prefix}_queue.csv"
        with open(path, "w", encoding="utf-8") as f:
            f.write("time,pending,running\n")
            for t, p, r in queue_samples:
                f.write(f"{t:.1f},{p},{r}\n")


class Entrepreneur(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        with _lock:
            self.scenario = next(_next_scenario)
        token, self.company_id = _tokens[self.scenario["login"]]
        self.client.headers["Authorization"] = f"Bearer {token}"
        self.done = 0

    def _call(self, method: str, url: str, name: str, **kw):
        """Запрос, который должен пройти: ошибка (например, 503 «сервис перегружен») — это несданная заявка.
        Пользователь не повторяет её до бесконечности, а завершает работу: доля таких заявок видна в отчёте."""
        resp = self.client.request(method, url, name=name, **kw)
        if not resp.ok:
            raise StopUser
        return resp

    @task
    def apply(self):
        s = self.scenario
        app = self._call(
            "POST",
            "/api/v1/applications",
            "POST /applications (черновик)",
            json={
                "company_id": self.company_id,
                "product_code": s["product"],
                "purpose": s["purpose"],
                "amount": s["amount"],
                "term_months": s["term"],
            },
        ).json()
        url = f"/api/v1/applications/{app['id']}"
        if s.get("fields"):
            self._call("PATCH", url, "PATCH /applications/{id} (черновик)", json=s["fields"])
        for doc in s.get("docs", []):
            self._call("POST", f"{url}/documents/demo", "POST .../documents/demo", params={"doc_type": doc})
        ch = self._call(
            "POST", f"{url}/sign-challenges", "POST .../sign-challenges", json={"purpose": "CONSENTS"}
        ).json()
        self._call(
            "POST",
            f"{url}/consents",
            "POST .../consents",
            json={"challenge_id": ch["challenge_id"], "code": ch["demo_code"]},
        )
        submitted = time.perf_counter()
        self._call("POST", f"{url}/submit", "POST .../submit")
        status = "SUBMITTED"
        while status in PROCESSING:
            gevent.sleep(POLL_SECONDS)
            status = self._call("GET", url, "GET /applications/{id} (опрос статуса)").json()["status"]
        events.request.fire(
            request_type="конвейер",
            name=f"отправка → решение [{s['login']}: {status}]",
            response_time=(time.perf_counter() - submitted) * 1000,
            response_length=0,
            exception=None,
            context={},
        )
        events.request.fire(
            request_type="конвейер",
            name="отправка → решение (все заявки)",
            response_time=(time.perf_counter() - submitted) * 1000,
            response_length=0,
            exception=None,
            context={},
        )
        self.done += 1
        if self.done >= APPLICATIONS_PER_USER:
            raise StopUser
