"""Форматирование чисел и фраз для текстов клиенту и банку: «1 250 000 ₽», «22%», «21,5%», «12 мес.»."""

from __future__ import annotations

NBSP = " "


def fmt_amount(value: float) -> str:
    """Число с разрядами через неразрывный пробел: 1 250 000."""
    return f"{float(value):,.0f}".replace(",", NBSP)


def fmt_money(value: float) -> str:
    return f"{fmt_amount(value)}{NBSP}₽"


def fmt_rate(value: float) -> str:
    """Ставка без лишнего нуля: 22% и 21,5%."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") + "%"


def months_range(low: int, high: int) -> str:
    return f"{low} мес." if low == high else f"{low}–{high} мес."


def lower_first(text: str) -> str:
    """Первая буква — строчная, если это не аббревиатура: «По данным ЕГРЮЛ» → «по данным ЕГРЮЛ», «ЕГРЮЛ» — без изменений."""
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        return text[0].lower() + text[1:]
    return text


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова для числа: 1 запрос, 2 запроса, 5 запросов, 21 запрос, 22 запроса."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
