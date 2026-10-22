"""Адаптер к внешним системам: ЕГРЮЛ/ЕГРИП, ФНС «Прозрачный бизнес», БКИ, АБС.

В поставке они заменены симулятором `integrations-mock` с упрощёнными контрактами.
Адаптер изолирует конвейер от транспорта: таймауты, журнал вызовов, корреляционный ID.
Повторные попытки с экспоненциальной задержкой делает воркер (см. worker/runner.py).
"""

from __future__ import annotations

import logging
import time
import uuid

import httpx

from app.config import get_settings
from app.db import SessionLocal
from app.models import IntegrationCall

log = logging.getLogger("integrations")


class IntegrationError(Exception):
    def __init__(self, service: str, message: str, retryable: bool = True):
        super().__init__(f"{service}: {message}")
        self.service = service
        self.retryable = retryable


SERVICE_TITLES = {
    "EGRUL": "ЕГРЮЛ/ЕГРИП",
    "FNS": "ФНС «Прозрачный бизнес»",
    "BKI": "Бюро кредитных историй",
    "ABS": "АБС банка",
}


class ExternalSystems:
    def __init__(self, application_id=None, attempt: int = 1, correlation_id: str | None = None):
        s = get_settings()
        self.application_id = application_id
        self.attempt = attempt
        self.correlation_id = correlation_id or uuid.uuid4().hex
        self._client = httpx.Client(
            base_url=s.integrations_base_url,
            timeout=s.integration_timeout_seconds,
            headers={"X-Request-ID": self.correlation_id},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ExternalSystems:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _log(self, service: str, operation: str, status: str, http_status, started: float, error: str | None):
        duration = int((time.perf_counter() - started) * 1000)
        # Журнал пишется в отдельной транзакции, чтобы сохраниться даже при откате этапа
        with SessionLocal() as db:
            db.add(
                IntegrationCall(
                    application_id=self.application_id,
                    service=service,
                    operation=operation,
                    status=status,
                    http_status=http_status,
                    duration_ms=duration,
                    attempt=self.attempt,
                    error=(error or "")[:500] or None,
                    correlation_id=self.correlation_id,
                )
            )
            db.commit()

    def _call(self, service: str, operation: str, method: str, url: str, **kwargs) -> dict:
        started = time.perf_counter()
        try:
            resp = self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            self._log(service, operation, "TIMEOUT", None, started, str(e))
            raise IntegrationError(service, "таймаут ответа") from e
        except httpx.HTTPError as e:
            self._log(service, operation, "ERROR", None, started, str(e))
            raise IntegrationError(service, "сервис недоступен") from e
        if resp.status_code >= 500:
            self._log(service, operation, "ERROR", resp.status_code, started, resp.text[:300])
            raise IntegrationError(service, f"ошибка сервиса (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            self._log(service, operation, "ERROR", resp.status_code, started, resp.text[:300])
            raise IntegrationError(service, f"запрос отклонён (HTTP {resp.status_code})", retryable=False)
        self._log(service, operation, "SUCCESS", resp.status_code, started, None)
        return resp.json()

    # ---- контракты (упрощённые, с сохранением смысла интеграции)

    def egrul(self, inn: str) -> dict:
        return self._call("EGRUL", "get_extract", "GET", f"/egrul/v1/entities/{inn}")

    def fns(self, inn: str) -> dict:
        return self._call("FNS", "transparent_business", "GET", f"/fns/v1/transparent-business/{inn}")

    def bki(self, inn: str, consent_id: str) -> dict:
        return self._call(
            "BKI",
            "credit_report",
            "POST",
            "/bki/v1/credit-reports",
            json={"inn": inn, "consent_id": consent_id, "purpose": "loan_application"},
        )

    def abs_disburse(self, payload: dict) -> dict:
        return self._call("ABS", "open_loan_and_disburse", "POST", "/abs/v1/loans", json=payload)

    def control_faults(self, payload: dict | None = None) -> dict:
        if payload is None:
            return self._client.get("/_control/faults").json()
        return self._client.post("/_control/faults", json=payload).json()
