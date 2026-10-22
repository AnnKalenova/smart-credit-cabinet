"""Скоринг: прозрачная балльная модель (scorecard).

Каждый фактор добавляет или вычитает баллы от базовых 600. Итоговый балл переводится в рейтинг A–E
и условную вероятность дефолта (PD). Модель детерминированная: по одним и тем же входным данным
всегда получается один и тот же результат, а вклад каждого фактора виден аналитику и клиенту.

Скоринг оценивает только риск. Итоговое решение (сумма, срок, ставка, ручное рассмотрение)
принимает кредитная политика в policy.py.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from datetime import date

MODEL_VERSION = "scorecard-1.0"
BASE_SCORE = 600
GRADE_THRESHOLDS: list[tuple[int, str]] = [(700, "A"), (650, "B"), (600, "C"), (550, "D")]
GRADE_PD = {"A": 0.008, "B": 0.02, "C": 0.045, "D": 0.09, "E": 0.18}
GRADE_LABELS = {
    "A": "Минимальный риск",
    "B": "Низкий риск",
    "C": "Умеренный риск",
    "D": "Повышенный риск",
    "E": "Высокий риск",
}

# Отрасли по первым двум цифрам ОКВЭД (упрощённая отраслевая карта риска)
INDUSTRY_LOW_RISK = {"10", "11", "21", "25", "26", "27", "28", "62", "63", "72", "85", "86"}
INDUSTRY_HIGH_RISK = {"41", "42", "43", "55", "56", "68", "92", "93"}


@dataclass
class TurnoverStats:
    months: int
    avg_monthly: float
    cv: float  # коэффициент вариации помесячных поступлений
    trend: float  # среднее за 3 последних месяца к среднему за предыдущие, минус 1
    series: list[dict] = field(default_factory=list)


def turnover_stats(rows: list[tuple[date, float, float]]) -> TurnoverStats:
    """rows: (месяц, поступления, списания), отсортированы по месяцу."""
    credits = [c for _, c, _ in rows]
    if not credits:
        return TurnoverStats(0, 0.0, 1.0, 0.0, [])
    avg = statistics.fmean(credits)
    cv = statistics.pstdev(credits) / avg if avg > 0 else 1.0
    if len(credits) >= 6:
        last3 = statistics.fmean(credits[-3:])
        prev = statistics.fmean(credits[:-3])
        trend = last3 / prev - 1 if prev > 0 else 0.0
    else:
        trend = 0.0
    series = [
        {"month": m.strftime("%Y-%m"), "credit": round(c, 2), "debit": round(d, 2)} for m, c, d in rows
    ]
    return TurnoverStats(len(credits), avg, cv, trend, series)


def months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


@dataclass
class ScoringInput:
    as_of: date
    company_kind: str
    registration_date: date
    client_since: date
    okved_code: str
    turnover: TurnoverStats
    effective_monthly_turnover: float  # обороты с учётом подтверждённой выручки в других банках
    new_monthly_payment: float  # платёж по запрашиваемому кредиту по индикативной ставке
    egrul: dict
    fns: dict
    bki: dict
    has_collateral: bool
    owner_guarantee: bool


@dataclass
class Factor:
    code: str
    name: str
    value: str
    points: int
    comment: str
    client_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScoringOutcome:
    score: int
    grade: str
    pd: float
    factors: list[Factor]
    metrics: dict

    @property
    def grade_label(self) -> str:
        return GRADE_LABELS[self.grade]


def _fmt_money(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f} млн ₽".replace(".", ",")
    if abs(v) >= 1_000:
        return f"{v / 1_000:.0f} тыс. ₽"
    return f"{v:.0f} ₽"


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def grade_for(score: int) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "E"


def score(inp: ScoringInput) -> ScoringOutcome:
    factors: list[Factor] = []

    # 1. Возраст бизнеса
    age = months_between(inp.registration_date, inp.as_of)
    if age < 12:
        pts, txt = -45, "Бизнес работает меньше года — данных о его устойчивости пока мало"
    elif age < 36:
        pts, txt = 0, "Бизнес работает от 1 до 3 лет"
    elif age < 60:
        pts, txt = 20, "Бизнес работает больше 3 лет"
    else:
        pts, txt = 35, "Бизнес работает больше 5 лет"
    factors.append(Factor("business_age", "Возраст бизнеса", f"{age} мес.", pts, txt, txt))

    # 2. Срок обслуживания в банке
    tenure = months_between(inp.client_since, inp.as_of)
    if tenure < 6:
        pts, txt = -15, "Клиент банка меньше полугода — короткая история операций"
    elif tenure < 24:
        pts, txt = 5, "Клиент банка от полугода до двух лет"
    else:
        pts, txt = 15, "Клиент банка больше двух лет"
    factors.append(Factor("bank_tenure", "Срок обслуживания в банке", f"{tenure} мес.", pts, txt, txt))

    # 3. Стабильность поступлений
    cv = inp.turnover.cv
    if cv < 0.15:
        pts, txt = 25, "Поступления на счёт стабильны от месяца к месяцу"
    elif cv < 0.35:
        pts, txt = 5, "Поступления колеблются умеренно"
    else:
        pts, txt = -20, "Поступления на счёт сильно колеблются"
    factors.append(
        Factor("turnover_stability", "Стабильность поступлений", f"вариация {_pct(cv)}", pts, txt, txt)
    )

    # 4. Динамика оборотов
    tr = inp.turnover.trend
    if tr >= 0.10:
        pts, txt = 15, "Обороты за последние 3 месяца выросли"
    elif tr >= -0.15:
        pts, txt = 0, "Обороты в последние месяцы без существенных изменений"
    else:
        pts, txt = -35, "Обороты за последние 3 месяца заметно снизились"
    factors.append(Factor("turnover_trend", "Динамика оборотов", f"{tr * 100:+.0f}%", pts, txt, txt))

    # 5. Долговая нагрузка (все платежи по кредитам / месячные поступления)
    existing = float(inp.bki.get("total_monthly_payment", 0) or 0)
    base = inp.effective_monthly_turnover
    dti = (existing + inp.new_monthly_payment) / base if base > 0 else 9.99
    if dti < 0.10:
        pts, txt = 40, "Платежи по кредитам займут меньше 10% поступлений"
    elif dti < 0.20:
        pts, txt = 20, "Платежи по кредитам займут 10–20% поступлений"
    elif dti < 0.30:
        pts, txt = 0, "Платежи по кредитам займут 20–30% поступлений"
    elif dti < 0.40:
        pts, txt = -30, "Платежи по кредитам займут 30–40% поступлений — высокая нагрузка"
    else:
        pts, txt = -70, "Платежи по кредитам превысят 40% поступлений — нагрузка слишком высокая"
    factors.append(Factor("debt_load", "Долговая нагрузка с учётом нового кредита", _pct(dti), pts, txt, txt))

    # 6. Кредитная история (БКИ)
    overdue12 = int(inp.bki.get("max_overdue_days_12m", 0) or 0)
    history = int(inp.bki.get("history_length_months", 0) or 0)
    if overdue12 > 90:
        pts, txt = -150, f"Просрочка более 90 дней за последний год ({overdue12} дн.)"
    elif overdue12 > 30:
        pts, txt = -80, f"Просрочка {overdue12} дн. за последний год"
    elif overdue12 > 0:
        pts, txt = -25, f"Небольшая просрочка {overdue12} дн. за последний год"
    elif history == 0:
        pts, txt = -5, "Кредитной истории пока нет"
    elif history >= 12:
        pts, txt = 40, "Кредиты погашались без просрочек"
    else:
        pts, txt = 10, "Короткая кредитная история без просрочек"
    factors.append(
        Factor(
            "credit_history",
            "Кредитная история",
            f"просрочка {overdue12} дн., история {history} мес.",
            pts,
            txt,
            txt,
        )
    )

    # 7. Кредитная активность: число запросов в БКИ за 30 дней
    inq = int(inp.bki.get("inquiries_30d", 0) or 0)
    if inq >= 5:
        pts, txt = -30, f"{inq} запросов кредитной истории за месяц — компания активно ищет финансирование"
    elif inq >= 3:
        pts, txt = -10, f"{inq} запроса кредитной истории за месяц"
    else:
        pts, txt = 0, "Обычная кредитная активность"
    factors.append(Factor("credit_inquiries", "Запросы в БКИ за 30 дней", str(inq), pts, txt, ""))

    # 8. Налоговая задолженность
    arrears = float(inp.fns.get("tax_arrears", 0) or 0)
    if arrears == 0:
        pts, txt = 10, "Нет задолженности по налогам"
    elif arrears <= 100_000:
        pts, txt = 0, "Небольшая задолженность по налогам"
    elif arrears <= 1_000_000:
        pts, txt = -30, "Есть задолженность по налогам"
    else:
        pts, txt = -60, "Крупная задолженность по налогам"
    factors.append(Factor("tax_arrears", "Задолженность по налогам", _fmt_money(arrears), pts, txt, txt))

    # 9. Отраслевой риск
    section = inp.okved_code[:2]
    if section in INDUSTRY_LOW_RISK:
        pts, txt = 10, "Отрасль с низким уровнем риска"
    elif section in INDUSTRY_HIGH_RISK:
        pts, txt = -15, "Отрасль с повышенным уровнем риска"
    else:
        pts, txt = 0, "Отрасль со средним уровнем риска"
    factors.append(Factor("industry", "Отрасль (ОКВЭД)", inp.okved_code, pts, txt, ""))

    # 10. Рентабельность (по отчётности юрлица)
    revenue = float(inp.fns.get("revenue_last_year") or 0)
    profit = inp.fns.get("net_profit_last_year")
    margin = None
    if inp.company_kind == "LEGAL" and revenue > 0 and profit is not None:
        margin = float(profit) / revenue
        if margin >= 0.10:
            pts, txt = 20, "Бизнес прибыльный: рентабельность выше 10%"
        elif margin >= 0:
            pts, txt = 5, "Бизнес прибыльный"
        else:
            pts, txt = -35, "По итогам года бизнес убыточен"
        value = f"{margin * 100:.1f}%"
    else:
        pts, txt, value = 0, "Нет публичной отчётности (ИП) — оценка по оборотам", "—"
    factors.append(Factor("profitability", "Рентабельность по чистой прибыли", value, pts, txt, txt if pts else ""))

    # 11. Признаки риска из реестров
    flags: list[str] = []
    pts = 0
    if inp.fns.get("suspended_accounts"):
        pts -= 50
        flags.append("приостановлены операции по счетам")
    if inp.fns.get("unreliable_data"):
        pts -= 40
        flags.append("недостоверные сведения в ЕГРЮЛ")
    if inp.fns.get("mass_address"):
        pts -= 25
        flags.append("адрес массовой регистрации")
    director_since = inp.egrul.get("director", {}).get("since")
    if director_since and months_between(date.fromisoformat(director_since), inp.as_of) < 6:
        pts -= 15
        flags.append("руководитель сменился менее 6 месяцев назад")
    txt = "Признаки риска: " + ", ".join(flags) if flags else "Негативных признаков в реестрах нет"
    factors.append(
        Factor("registry_flags", "Сведения ЕГРЮЛ и ФНС", f"{len(flags)} призн.", pts, txt, "" if flags else txt)
    )

    # 12. Обеспечение
    pts = 0
    parts = []
    if inp.has_collateral:
        pts += 20
        parts.append("залог")
    if inp.owner_guarantee:
        pts += 10
        parts.append("поручительство собственника")
    txt = "Обеспечение: " + " и ".join(parts) if parts else "Без обеспечения"
    factors.append(Factor("security", "Обеспечение", ", ".join(parts) or "нет", pts, txt, txt if parts else ""))

    total = BASE_SCORE + sum(f.points for f in factors)
    total = max(300, min(900, total))
    grade = grade_for(total)

    metrics = {
        "business_age_months": age,
        "bank_tenure_months": tenure,
        "avg_monthly_turnover": round(inp.turnover.avg_monthly, 2),
        "effective_monthly_turnover": round(base, 2),
        "turnover_cv": round(cv, 4),
        "turnover_trend": round(tr, 4),
        "existing_monthly_payments": round(existing, 2),
        "new_monthly_payment": round(inp.new_monthly_payment, 2),
        "dti": round(dti, 4),
        "bki_score": inp.bki.get("score"),
        "max_overdue_days_12m": overdue12,
        "inquiries_30d": inq,
        "tax_arrears": arrears,
        "revenue_last_year": revenue or None,
        "net_profit_last_year": profit,
        "margin": round(margin, 4) if margin is not None else None,
        "registry_flags": flags,
    }
    return ScoringOutcome(score=total, grade=grade, pd=GRADE_PD[grade], factors=factors, metrics=metrics)
