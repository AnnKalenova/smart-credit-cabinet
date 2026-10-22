"""Симулятор внешних систем для кредитного конвейера.

Воспроизводит в сокращённом виде контракты:
  • ЕГРЮЛ/ЕГРИП (выписка о юрлице/ИП),
  • ФНС «Прозрачный бизнес» (налоговые признаки, отчётность),
  • Бюро кредитных историй (кредитный отчёт по согласию),
  • АБС банка (открытие ссудного счёта и выдача).
Данные синтетические и берутся из data/synthetic/companies.json.

Для демонстрации отказоустойчивости любой сервис можно «уронить» или «замедлить»:
POST /_control/faults {"service": "BKI", "mode": "down", "duration_seconds": 120}
"""

from __future__ import annotations

import asyncio
import calendar
import json
import os
import random
import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

DATA_PATH = Path(
    os.getenv("SYNTHETIC_DATA_PATH", Path(__file__).resolve().parents[2] / "data" / "synthetic" / "companies.json")
)
LATENCY_MIN_MS = int(os.getenv("MOCK_LATENCY_MIN_MS", "150"))
LATENCY_MAX_MS = int(os.getenv("MOCK_LATENCY_MAX_MS", "600"))
SLOW_SECONDS = float(os.getenv("MOCK_SLOW_SECONDS", "8"))

app = FastAPI(
    title="Симулятор внешних систем (ЕГРЮЛ, ФНС, БКИ, АБС)",
    version="1.0.0",
    description=__doc__,
    docs_url="/docs",
)

_data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
COMPANIES = {c["inn"]: c for c in _data["companies"]}
FAULTS: dict[str, dict] = {}  # service -> {"mode": ..., "until": epoch}
LOANS: dict[str, dict] = {}  # idempotency_key -> ответ АБС


def rel_date(spec: dict | None) -> str | None:
    if not spec:
        return None
    today = date.today()
    month_index = today.month - 1 - int(spec["months_ago"])
    year = today.year + month_index // 12
    month = month_index % 12 + 1
    day = min(int(spec.get("day", 1)), 28, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


async def simulate(service: str) -> None:
    fault = FAULTS.get(service)
    if fault and fault["until"] < time.time():
        FAULTS.pop(service, None)
        fault = None
    if fault and fault["mode"] == "down":
        raise HTTPException(503, f"{service}: сервис временно недоступен (смоделированный сбой)")
    if fault and fault["mode"] == "slow":
        await asyncio.sleep(SLOW_SECONDS)
    await asyncio.sleep(random.uniform(LATENCY_MIN_MS, LATENCY_MAX_MS) / 1000)


def company_or_404(inn: str) -> dict:
    c = COMPANIES.get(inn)
    if c is None:
        raise HTTPException(404, "Сведения не найдены")
    return c


@app.get("/health")
def health():
    return {"status": "ok", "companies": len(COMPANIES)}


# --------------------------------------------------------------------------- ЕГРЮЛ / ЕГРИП


@app.get("/egrul/v1/entities/{inn}", tags=["ЕГРЮЛ/ЕГРИП"], summary="Выписка из реестра")
async def egrul(inn: str):
    await simulate("EGRUL")
    c = company_or_404(inn)
    e = c["egrul"]
    director = {**e["director"], "since": rel_date(e["director"].get("since"))}
    result = {
        "source": "ЕГРЮЛ/ЕГРИП (симулятор)",
        "extract_id": uuid.uuid4().hex[:12],
        "extracted_at": datetime.now(UTC).isoformat(),
        "inn": c["inn"],
        "ogrn": c["ogrn"],
        "kind": c["kind"],
        "full_name": c["full_name"],
        "short_name": c["short_name"],
        "status": e["status"],
        "status_text": e["status_text"],
        "status_since": rel_date(e.get("status_since")),
        "registration_date": rel_date(c["registration"]),
        "okved_main": c["okved"],
        "address": c["address"],
        "director": director,
        "founders": e.get("founders", []),
        "authorized_capital": e.get("authorized_capital"),
    }
    if e.get("previous_director"):
        result["previous_director"] = {**e["previous_director"], "until": rel_date(e["previous_director"].get("until"))}
    return result


# --------------------------------------------------------------------------- ФНС «Прозрачный бизнес»


@app.get("/fns/v1/transparent-business/{inn}", tags=["ФНС"], summary="Сведения сервиса «Прозрачный бизнес»")
async def fns(inn: str):
    await simulate("FNS")
    c = company_or_404(inn)
    return {
        "source": "ФНС «Прозрачный бизнес» (симулятор)",
        "inn": c["inn"],
        "reporting_year": date.today().year - 1,
        **c["fns"],
    }


# --------------------------------------------------------------------------- БКИ


class BkiRequest(BaseModel):
    inn: str
    consent_id: str = Field(min_length=1, description="ID подписанного согласия на запрос кредитной истории")
    purpose: str = "loan_application"


@app.post("/bki/v1/credit-reports", tags=["БКИ"], summary="Кредитный отчёт по согласию субъекта")
async def bki(body: BkiRequest):
    await simulate("BKI")
    c = COMPANIES.get(body.inn)
    report_id = uuid.uuid4().hex[:16]
    if c is None:
        return {
            "source": "БКИ (симулятор)",
            "report_id": report_id,
            "inn": body.inn,
            "found": False,
            "history_length_months": 0,
            "loans": [],
            "total_monthly_payment": 0,
        }
    b = c["bki"]
    loans = [{**ln, "opened": rel_date(ln.get("opened"))} for ln in b["loans"]]
    active = [ln for ln in loans if ln["status"] == "ACTIVE"]
    return {
        "source": "БКИ (симулятор)",
        "report_id": report_id,
        "inn": body.inn,
        "found": True,
        "consent_id": body.consent_id,
        "score": b["score"],
        "history_length_months": b["history_length_months"],
        "inquiries_30d": b["inquiries_30d"],
        "max_overdue_days_12m": b["max_overdue_days_12m"],
        "max_overdue_days_ever": b["max_overdue_days_ever"],
        "current_overdue_days": b["current_overdue_days"],
        "active_loans": len(active),
        "total_outstanding": sum(ln["outstanding"] for ln in active),
        "total_monthly_payment": sum(ln["monthly_payment"] for ln in active),
        "loans": loans,
    }


# --------------------------------------------------------------------------- АБС


class DisburseRequest(BaseModel):
    inn: str
    contract_number: str
    product_code: str
    amount: float = Field(gt=0)
    term_months: int = Field(gt=0)
    rate: float = Field(gt=0)
    settlement_account: str
    idempotency_key: str


@app.post("/abs/v1/loans", tags=["АБС"], summary="Открыть ссудный счёт и перечислить кредит (идемпотентно)")
async def abs_disburse(body: DisburseRequest):
    await simulate("ABS")
    if body.idempotency_key in LOANS:
        return LOANS[body.idempotency_key]
    prefix = "45206" if body.inn and len(body.inn) == 10 else "45406"
    loan_account = f"{prefix}810{random.randint(0, 999_999_999_999):012d}"
    result = {
        "source": "АБС (симулятор)",
        "operation_id": uuid.uuid4().hex,
        "status": "DISBURSED",
        "loan_account": loan_account,
        "settlement_account": body.settlement_account,
        "amount": body.amount,
        "disbursed_at": datetime.now(UTC).isoformat(),
    }
    LOANS[body.idempotency_key] = result
    return result


# --------------------------------------------------------------------------- управление сбоями


class FaultIn(BaseModel):
    service: Literal["EGRUL", "FNS", "BKI", "ABS"]
    mode: Literal["ok", "down", "slow"]
    duration_seconds: int = Field(default=120, ge=10, le=3600)


def _faults_view() -> dict:
    now = time.time()
    return {
        s: {"mode": f["mode"], "seconds_left": int(f["until"] - now)} for s, f in FAULTS.items() if f["until"] > now
    }


@app.get("/_control/faults", tags=["Управление симулятором"])
def get_faults():
    return {"faults": _faults_view()}


@app.post("/_control/faults", tags=["Управление симулятором"])
def set_fault(body: FaultIn, request: Request):
    if body.mode == "ok":
        FAULTS.pop(body.service, None)
    else:
        FAULTS[body.service] = {"mode": body.mode, "until": time.time() + body.duration_seconds}
    return {"faults": _faults_view()}
