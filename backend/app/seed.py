"""Загрузка синтетических данных: продукты, параметры политики, демо-компании, пользователи, обороты.

Запуск: python -m app.seed  (идемпотентно: повторный запуск ничего не дублирует)
"""

from __future__ import annotations

import json
import logging
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.enums import POSITION_LABELS, Role
from app.domain.finance import add_months
from app.domain.products import PRODUCTS_SEED
from app.logging_setup import setup_logging
from app.models import AccountTurnover, Company, CompanyMember, Product, Setting, User
from app.security import hash_password
from app.services.workflow import DEFAULT_SETTINGS

log = logging.getLogger("seed")
settings = get_settings()


def _data_path() -> Path:
    p = Path(settings.synthetic_data_path)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parent.parent / p).resolve()
    return p


def load_data() -> dict:
    return json.loads(_data_path().read_text(encoding="utf-8"))


def rel_date(spec: dict, today: date | None = None) -> date:
    """{"months_ago": N, "day": D} → дата N месяцев назад (день не больше 28)."""
    today = today or date.today()
    d = add_months(date(today.year, today.month, 1), -int(spec["months_ago"]))
    return date(d.year, d.month, min(int(spec.get("day", 1)), 28))


def turnover_series(spec: dict, today: date | None = None) -> list[tuple[date, float, float, float]]:
    today = today or date.today()
    rnd = random.Random(spec["seed"])
    months = int(spec["months"])
    first_of_month = date(today.year, today.month, 1)
    rows = []
    for i in range(months):
        month = add_months(first_of_month, -(months - i))
        trend = (1 + spec["monthly_growth"]) ** i
        noise = max(-0.6, min(0.6, rnd.gauss(0, spec["noise"])))
        credit = round(spec["base"] * trend * (1 + noise), -3)
        debit = round(credit * spec["debit_ratio"] * (1 + rnd.uniform(-0.03, 0.03)), -3)
        balance = round(credit * rnd.uniform(0.08, 0.2), -3)
        rows.append((month, credit, debit, balance))
    return rows


def _demo_accounts(data: dict) -> list[dict]:
    pwd = data["demo_password"]
    result = []
    for c in data["companies"]:
        for u in c["users"]:
            title = f"{POSITION_LABELS[u['position']]}, {c['short_name']}"
            if not u["can_sign"]:
                title += " (без права подписи)"
            result.append(
                {
                    "login": u["login"],
                    "password": pwd,
                    "full_name": u["full_name"],
                    "role": Role.CLIENT,
                    "title": title,
                    "company": c["short_name"],
                    "scenario": (
                        f"{c['scenario']}: {c['scenario_hint']}"
                        if u["can_sign"]
                        else "Бухгалтер готовит заявку, но подписать и отправить её может только руководитель"
                    ),
                }
            )
    for s in data["staff"]:
        result.append(
            {
                "login": s["login"],
                "password": pwd,
                "full_name": s["full_name"],
                "role": s["role"],
                "title": s["title"],
                "company": None,
                "scenario": (
                    "Очередь ручного рассмотрения, досье заявки, решение, мониторинг конвейера"
                    if s["role"] == Role.ANALYST
                    else "Ключевая ставка и параметры политики, моделирование сбоев внешних систем"
                ),
            }
        )
    return result


try:
    DEMO_ACCOUNTS = _demo_accounts(load_data())
except FileNotFoundError:  # pragma: no cover — например, при генерации OpenAPI без данных
    DEMO_ACCOUNTS = []


def sync_products(db: Session) -> None:
    for spec in PRODUCTS_SEED:
        p = db.get(Product, spec["code"])
        values = {**spec, "purposes": [str(x) for x in spec["purposes"]], "doc_rules": [
            {**r, "doc_type": str(r["doc_type"]), **({"value": str(r["value"])} if r.get("when") == "purpose" else {})}
            for r in spec["doc_rules"]
        ]}
        if p is None:
            db.add(Product(**values))
        else:
            for k, v in values.items():
                setattr(p, k, v)


def seed(db: Session) -> bool:
    db.execute(text("SELECT pg_advisory_xact_lock(424242)"))  # защита от одновременного сидирования
    sync_products(db)
    for key, (value, description) in DEFAULT_SETTINGS.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value, description=description, updated_by="Начальная настройка"))
    if db.scalar(select(User).limit(1)) is not None:
        db.commit()
        log.info("демо-данные уже загружены")
        return False

    data = load_data()
    pwd_hash = hash_password(data["demo_password"])
    today = date.today()
    for c in data["companies"]:
        company = Company(
            inn=c["inn"],
            ogrn=c["ogrn"],
            kind=c["kind"],
            short_name=c["short_name"],
            full_name=c["full_name"],
            okved_code=c["okved"]["code"],
            okved_name=c["okved"]["name"],
            registration_date=rel_date(c["registration"], today),
            legal_address=c["address"],
            client_since=rel_date(c["client_since"], today),
            segment=c["segment"],
            settlement_account=c["settlement_account"],
            headcount=c["headcount"],
        )
        db.add(company)
        db.flush()
        for month, credit, debit, balance in turnover_series(c["turnover"], today):
            db.add(
                AccountTurnover(
                    company_id=company.id,
                    month=month,
                    credit_turnover=Decimal(str(credit)),
                    debit_turnover=Decimal(str(debit)),
                    avg_balance=Decimal(str(balance)),
                )
            )
        for u in c["users"]:
            user = User(
                login=u["login"],
                password_hash=pwd_hash,
                full_name=u["full_name"],
                role=Role.CLIENT,
                position_title=POSITION_LABELS[u["position"]],
                phone=u["phone"],
            )
            db.add(user)
            db.flush()
            db.add(CompanyMember(company_id=company.id, user_id=user.id, position=u["position"], can_sign=u["can_sign"]))
    for s in data["staff"]:
        db.add(User(login=s["login"], password_hash=pwd_hash, full_name=s["full_name"], role=s["role"], position_title=s["title"]))
    db.commit()
    log.info("демо-данные загружены", extra={"companies": len(data["companies"])})
    return True


def main() -> None:
    from app.db import SessionLocal

    setup_logging()
    with SessionLocal() as db:
        seed(db)


if __name__ == "__main__":
    main()
