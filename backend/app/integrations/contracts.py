"""Обязательные данные в ответах внешних систем.

Ответ без нужных полей нельзя считать благополучным: пустая выписка ЕГРЮЛ не подтверждает, что компания
действует, а отчёт БКИ без сведений о просрочках — не чистая кредитная история. Такой ответ считается сбоем
источника: воркер повторяет запрос, а если полный ответ так и не пришёл — передаёт заявку специалисту.

Проверяются только поля, на которых строятся скоринг и политика, и то, что ответ относится к той же компании.
Остальные поля сохраняются как есть: в досье специалист видит ответ источника целиком.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

Days = Annotated[int, Field(ge=0)]
Money = Annotated[float, Field(ge=0)]
Text = Annotated[str, Field(min_length=1)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="allow")


class Director(_Contract):
    name: Text
    # без даты назначения не проверить, не сменился ли руководитель недавно, — это причина ручного рассмотрения
    since: date


class EgrulExtract(_Contract):
    inn: Text
    ogrn: Annotated[str, Field(pattern=r"^\d{13}(\d{2})?$")]
    kind: Literal["LEGAL", "IP"]
    status: Text
    status_text: Text
    registration_date: date
    director: Director


class FnsReport(_Contract):
    inn: Text
    reporting_year: int
    tax_arrears: Money
    suspended_accounts: StrictBool
    unreliable_data: StrictBool
    mass_address: StrictBool
    # у нового бизнеса отчётности может ещё не быть, но поле в ответе должно быть
    revenue_last_year: float | None
    net_profit_last_year: float | None


# без них нельзя проверить просрочки и запросы; балла бюро у субъекта без истории может не быть
BKI_HISTORY_FIELDS = (
    "inquiries_30d",
    "max_overdue_days_12m",
    "max_overdue_days_ever",
    "current_overdue_days",
)


class BkiReport(_Contract):
    inn: Text
    found: StrictBool
    history_length_months: Days
    total_monthly_payment: Money
    loans: list[dict[str, Any]]
    score: int | None = None
    inquiries_30d: Days | None = None
    max_overdue_days_12m: Days | None = None
    max_overdue_days_ever: Days | None = None
    current_overdue_days: Days | None = None

    @model_validator(mode="after")
    def history_is_complete(self) -> BkiReport:
        # «История не найдена» — законный ответ, а найденная история без сведений о просрочках — нет
        if self.found:
            missing = [f for f in BKI_HISTORY_FIELDS if getattr(self, f) is None]
            if missing:
                raise ValueError("нет полей " + ", ".join(missing))
        return self


class AbsDisbursement(_Contract):
    status: Literal["DISBURSED"]
    loan_account: Annotated[str, Field(pattern=r"^\d{20}$")]
    amount: Annotated[float, Field(gt=0)]


CONTRACTS: dict[str, type[_Contract]] = {
    "EGRUL": EgrulExtract,
    "FNS": FnsReport,
    "BKI": BkiReport,
    "ABS": AbsDisbursement,
}


def _explain(error: ValidationError) -> str:
    missing: list[str] = []
    invalid: list[str] = []
    other: list[str] = []
    for e in error.errors():
        field = ".".join(str(p) for p in e["loc"])
        if not field:
            other.append(str(e["msg"]).removeprefix("Value error, "))
        elif e["type"] == "missing":
            missing.append(field)
        else:
            invalid.append(field)
    parts = []
    if missing:
        parts.append("нет полей " + ", ".join(dict.fromkeys(missing)))
    if invalid:
        parts.append("некорректные значения: " + ", ".join(dict.fromkeys(invalid)))
    parts.extend(other)
    return "неполный ответ: " + "; ".join(parts)


def contract_problem(service: str, data: Any, expected: dict[str, Any] | None = None) -> str | None:
    """None — в ответе есть всё нужное. Иначе — что не так: для журнала вызовов и специалиста.

    expected — значения, которые ответ обязан повторить: ИНН запрошенной компании, сумма выдачи."""
    if not isinstance(data, dict):
        return "неполный ответ: вместо объекта JSON пришло другое значение"
    if not data:
        return "пустой ответ: нет ни одного обязательного поля"
    try:
        parsed = CONTRACTS[service].model_validate(data)
    except ValidationError as e:
        return _explain(e)
    for field, value in (expected or {}).items():
        got = getattr(parsed, field)
        same = abs(float(got) - float(value)) < 0.01 if isinstance(value, int | float) else got == value
        if not same:
            return f"ответ не совпадает с запросом: {field} = {got}, запрошено {value}"
    return None
