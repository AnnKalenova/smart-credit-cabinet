"""Финансовая математика: аннуитет, график платежей, округления."""

from __future__ import annotations

import calendar
import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def floor_to(value: float, step: int) -> int:
    if value <= 0:
        return 0
    return int(math.floor(value / step) * step)


def monthly_rate(annual_rate_pct: float) -> float:
    return annual_rate_pct / 100.0 / 12.0


def annuity_payment(amount: float, annual_rate_pct: float, months: int) -> float:
    """Ежемесячный аннуитетный платёж."""
    r = monthly_rate(annual_rate_pct)
    if months <= 0:
        raise ValueError("months must be positive")
    if r == 0:
        return amount / months
    return amount * r / (1 - (1 + r) ** -months)


def annuity_principal(payment: float, annual_rate_pct: float, months: int) -> float:
    """Какую сумму можно взять, если платить `payment` в месяц (обратная задача аннуитета)."""
    if payment <= 0:
        return 0.0
    r = monthly_rate(annual_rate_pct)
    if r == 0:
        return payment * months
    return payment * (1 - (1 + r) ** -months) / r


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def build_annuity_schedule(amount: float, annual_rate_pct: float, months: int, start: date) -> list[dict]:
    r = monthly_rate(annual_rate_pct)
    payment = round(annuity_payment(amount, annual_rate_pct, months), 2)
    balance = round(amount, 2)
    rows: list[dict] = []
    for n in range(1, months + 1):
        interest = round(balance * r, 2)
        if n == months:
            principal = balance
        else:
            principal = round(payment - interest, 2)
        balance = round(balance - principal, 2)
        rows.append(
            {
                "n": n,
                "date": add_months(start, n).isoformat(),
                "payment": round(principal + interest, 2),
                "principal": round(principal, 2),
                "interest": interest,
                "balance": max(balance, 0.0),
            }
        )
    return rows


def build_revolving_schedule(limit: float, annual_rate_pct: float, months: int, start: date) -> list[dict]:
    """Овердрафт: проценты только на использованную сумму; пример — лимит использован полностью."""
    r = monthly_rate(annual_rate_pct)
    interest = round(limit * r, 2)
    rows = []
    for n in range(1, months + 1):
        principal = round(limit, 2) if n == months else 0.0
        rows.append(
            {
                "n": n,
                "date": add_months(start, n).isoformat(),
                "payment": round(interest + principal, 2),
                "principal": principal,
                "interest": interest,
                "balance": 0.0 if n == months else round(limit, 2),
            }
        )
    return rows


def offer_economics(amount: float, rate: float, months: int, repayment_type: str) -> tuple[float, float]:
    """(ежемесячный платёж, переплата по процентам)."""
    if repayment_type == "REVOLVING":
        monthly = round(amount * monthly_rate(rate), 2)
        return monthly, round(monthly * months, 2)
    payment = round(annuity_payment(amount, rate, months), 2)
    total_interest = round(payment * months - amount, 2)
    return payment, total_interest


def inn_is_valid(inn: str) -> bool:
    """Проверка контрольных цифр ИНН (10 знаков — юрлицо, 12 — ИП/физлицо)."""
    if not inn.isdigit() or len(inn) not in (10, 12):
        return False
    digits = [int(c) for c in inn]

    def checksum(coefs: list[int], ds: list[int]) -> int:
        return sum(c * d for c, d in zip(coefs, ds, strict=False)) % 11 % 10

    if len(inn) == 10:
        return checksum([2, 4, 10, 3, 5, 9, 4, 6, 8], digits) == digits[9]
    c11 = checksum([7, 2, 4, 10, 3, 5, 9, 4, 6, 8], digits)
    c12 = checksum([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], digits)
    return c11 == digits[10] and c12 == digits[11]
