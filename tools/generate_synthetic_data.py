"""Генератор синтетических данных демо-компаний.

Создаёт data/synthetic/companies.json — единый источник данных для основного сервиса
(профиль клиента, обороты по счёту, пользователи) и симулятора внешних систем
(ЕГРЮЛ/ЕГРИП, ФНС, БКИ). ИНН и ОГРН вычисляются с корректными контрольными цифрами,
но начинаются с кода региона и инспекции «0000», которого не существует, — совпадение
с реальными компаниями исключено.

Даты заданы относительно текущей даты (months_ago), чтобы сценарии вели себя одинаково
в любой день проверки.

Запуск: python tools/generate_synthetic_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "synthetic" / "companies.json"


def inn_legal(serial: int) -> str:
    base = f"0000{serial:05d}"
    coefs = [2, 4, 10, 3, 5, 9, 4, 6, 8]
    check = sum(int(d) * c for d, c in zip(base, coefs, strict=True)) % 11 % 10
    return base + str(check)


def inn_person(serial: int) -> str:
    base = f"0000{serial:06d}"
    c11 = sum(int(d) * c for d, c in zip(base, [7, 2, 4, 10, 3, 5, 9, 4, 6, 8], strict=True)) % 11 % 10
    base += str(c11)
    c12 = sum(int(d) * c for d, c in zip(base, [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8], strict=True)) % 11 % 10
    return base + str(c12)


def ogrn(year: int, serial: int) -> str:
    base = f"1{year % 100:02d}0000{serial:05d}"
    return base + str(int(base) % 11 % 10)


def ogrnip(year: int, serial: int) -> str:
    base = f"3{year % 100:02d}00{serial:09d}"
    return base + str(int(base) % 13 % 10)


def account(prefix: str, serial: int) -> str:
    return f"{prefix}8100000{serial:08d}"[:20]


COMPANIES = [
    {
        "key": "bakery",
        "scenario": "Автоматическое одобрение",
        "scenario_hint": "Оборотный кредит 3 млн ₽ на 12 мес. — решение за несколько секунд без участия сотрудника",
        "inn": inn_legal(10471),
        "ogrn": ogrn(2018, 10471),
        "kind": "LEGAL",
        "short_name": "ООО «Северный хлеб»",
        "full_name": "Общество с ограниченной ответственностью «Северный хлеб»",
        "okved": {"code": "10.71", "name": "Производство хлеба и мучных кондитерских изделий недлительного хранения"},
        "registration": {"months_ago": 103, "day": 14},
        "client_since": {"months_ago": 92, "day": 1},
        "address": "603000, Нижегородская обл., г. Нижний Новгород, ул. Пекарская, д. 12",
        "segment": "Малое предприятие",
        "headcount": 28,
        "settlement_account": account("40702", 1001),
        "turnover": {
            "base": 4_500_000,
            "monthly_growth": 0.008,
            "noise": 0.05,
            "seed": 101,
            "months": 12,
            "debit_ratio": 0.94,
        },
        "egrul": {
            "status": "ACTIVE",
            "status_text": "Действующее",
            "director": {
                "name": "Смирнов Алексей Викторович",
                "position": "Генеральный директор",
                "since": {"months_ago": 103, "day": 14},
            },
            "founders": [
                {"name": "Смирнов Алексей Викторович", "share": 70},
                {"name": "Смирнова Елена Николаевна", "share": 30},
            ],
            "authorized_capital": 10000,
        },
        "fns": {
            "tax_regime": "УСН «доходы минус расходы»",
            "smb_category": "Малое предприятие",
            "avg_headcount": 28,
            "revenue_last_year": 52_400_000,
            "net_profit_last_year": 5_700_000,
            "tax_arrears": 0,
            "suspended_accounts": False,
            "mass_address": False,
            "unreliable_data": False,
            "disqualified_persons": False,
        },
        "bki": {
            "score": 768,
            "history_length_months": 64,
            "inquiries_30d": 1,
            "max_overdue_days_12m": 0,
            "max_overdue_days_ever": 3,
            "current_overdue_days": 0,
            "loans": [
                {
                    "creditor": "Банк «Восток» (синтетический)",
                    "type": "Кредит на оборудование",
                    "amount": 2_500_000,
                    "outstanding": 620_000,
                    "monthly_payment": 78_000,
                    "opened": {"months_ago": 31, "day": 15},
                    "status": "ACTIVE",
                    "max_overdue_days_12m": 0,
                }
            ],
        },
        "users": [
            {
                "login": "smirnov",
                "full_name": "Смирнов Алексей Викторович",
                "position": "DIRECTOR",
                "can_sign": True,
                "phone": "+7 900 000-01-01",
            },
            {
                "login": "orlova",
                "full_name": "Орлова Мария Петровна",
                "position": "ACCOUNTANT",
                "can_sign": False,
                "phone": "+7 900 000-01-02",
            },
        ],
        "demo_request": {
            "product_code": "WORKING_CAPITAL",
            "purpose": "WORKING_CAPITAL",
            "amount": 3_000_000,
            "term_months": 12,
            "purpose_details": "Закупка муки и сахара перед новогодним сезоном",
        },
    },
    {
        "key": "salon",
        "scenario": "Встречное предложение",
        "scenario_hint": "Экспресс-кредит 1 млн ₽ на 12 мес. — нагрузка высокая, банк предложит больший срок или меньшую сумму",
        "inn": inn_person(220512),
        "ogrn": ogrnip(2024, 220512),
        "kind": "IP",
        "short_name": "ИП Кузнецова А. С.",
        "full_name": "Индивидуальный предприниматель Кузнецова Анна Сергеевна",
        "okved": {"code": "96.02", "name": "Предоставление услуг парикмахерскими и салонами красоты"},
        "registration": {"months_ago": 32, "day": 12},
        "client_since": {"months_ago": 31, "day": 1},
        "address": "603005, Нижегородская обл., г. Нижний Новгород, ул. Садовая, д. 7, пом. 3",
        "segment": "Микропредприятие",
        "headcount": 7,
        "settlement_account": account("40802", 2002),
        "turnover": {
            "base": 950_000,
            "monthly_growth": 0.0,
            "noise": 0.08,
            "seed": 202,
            "months": 12,
            "debit_ratio": 0.97,
        },
        "egrul": {
            "status": "ACTIVE",
            "status_text": "Действующий ИП",
            "director": {
                "name": "Кузнецова Анна Сергеевна",
                "position": "Индивидуальный предприниматель",
                "since": {"months_ago": 32, "day": 12},
            },
            "founders": [],
            "authorized_capital": None,
        },
        "fns": {
            "tax_regime": "УСН «доходы»",
            "smb_category": "Микропредприятие",
            "avg_headcount": 7,
            "revenue_last_year": 10_900_000,
            "net_profit_last_year": None,
            "tax_arrears": 0,
            "suspended_accounts": False,
            "mass_address": False,
            "unreliable_data": False,
            "disqualified_persons": False,
        },
        "bki": {
            "score": 702,
            "history_length_months": 46,
            "inquiries_30d": 2,
            "max_overdue_days_12m": 0,
            "max_overdue_days_ever": 0,
            "current_overdue_days": 0,
            "loans": [
                {
                    "creditor": "Банк «Ока» (синтетический)",
                    "type": "Кредит на развитие бизнеса",
                    "amount": 2_000_000,
                    "outstanding": 1_150_000,
                    "monthly_payment": 88_000,
                    "opened": {"months_ago": 14, "day": 3},
                    "status": "ACTIVE",
                    "max_overdue_days_12m": 0,
                },
                {
                    "creditor": "Лизинговая компания «Опора» (синтетическая)",
                    "type": "Лизинг оборудования",
                    "amount": 3_400_000,
                    "outstanding": 2_300_000,
                    "monthly_payment": 145_000,
                    "opened": {"months_ago": 9, "day": 20},
                    "status": "ACTIVE",
                    "max_overdue_days_12m": 0,
                },
                {
                    "creditor": "Банк «Ока» (синтетический)",
                    "type": "Автокредит (физлицо)",
                    "amount": 1_800_000,
                    "outstanding": 950_000,
                    "monthly_payment": 52_000,
                    "opened": {"months_ago": 26, "day": 8},
                    "status": "ACTIVE",
                    "max_overdue_days_12m": 0,
                },
            ],
        },
        "users": [
            {
                "login": "kuznetsova",
                "full_name": "Кузнецова Анна Сергеевна",
                "position": "OWNER",
                "can_sign": True,
                "phone": "+7 900 000-02-01",
            },
        ],
        "demo_request": {
            "product_code": "EXPRESS",
            "purpose": "EXPANSION",
            "amount": 1_000_000,
            "term_months": 12,
            "purpose_details": "Ремонт второго зала и покупка трёх парикмахерских кресел",
        },
    },
    {
        "key": "technomash",
        "scenario": "Ручное рассмотрение → одобрение",
        "scenario_hint": "Инвестиционный кредит 25 млн ₽ на 60 мес. — сумма выше лимита автоматического решения, решение принимает аналитик",
        "inn": inn_legal(28411),
        "ogrn": ogrn(2016, 28411),
        "kind": "LEGAL",
        "short_name": "ООО «ТехноМаш НН»",
        "full_name": "Общество с ограниченной ответственностью «ТехноМаш НН»",
        "okved": {"code": "28.41", "name": "Производство металлообрабатывающего оборудования"},
        "registration": {"months_ago": 121, "day": 1},
        "client_since": {"months_ago": 117, "day": 10},
        "address": "603109, Нижегородская обл., г. Нижний Новгород, ул. Станкостроительная, д. 4, корп. 2",
        "segment": "Малое предприятие",
        "headcount": 64,
        "settlement_account": account("40702", 3003),
        "turnover": {
            "base": 18_500_000,
            "monthly_growth": 0.015,
            "noise": 0.06,
            "seed": 303,
            "months": 12,
            "debit_ratio": 0.95,
        },
        "egrul": {
            "status": "ACTIVE",
            "status_text": "Действующее",
            "director": {
                "name": "Лебедев Игорь Олегович",
                "position": "Генеральный директор",
                "since": {"months_ago": 121, "day": 1},
            },
            "founders": [
                {"name": "Лебедев Игорь Олегович", "share": 51},
                {"name": "Захаров Павел Андреевич", "share": 49},
            ],
            "authorized_capital": 500000,
        },
        "fns": {
            "tax_regime": "ОСН",
            "smb_category": "Малое предприятие",
            "avg_headcount": 64,
            "revenue_last_year": 228_000_000,
            "net_profit_last_year": 24_100_000,
            "tax_arrears": 0,
            "suspended_accounts": False,
            "mass_address": False,
            "unreliable_data": False,
            "disqualified_persons": False,
        },
        "bki": {
            "score": 791,
            "history_length_months": 96,
            "inquiries_30d": 1,
            "max_overdue_days_12m": 0,
            "max_overdue_days_ever": 0,
            "current_overdue_days": 0,
            "loans": [
                {
                    "creditor": "Лизинговая компания «Станок-Финанс» (синтетическая)",
                    "type": "Лизинг",
                    "amount": 9_800_000,
                    "outstanding": 3_200_000,
                    "monthly_payment": 185_000,
                    "opened": {"months_ago": 40, "day": 18},
                    "status": "ACTIVE",
                    "max_overdue_days_12m": 0,
                }
            ],
        },
        "users": [
            {
                "login": "lebedev",
                "full_name": "Лебедев Игорь Олегович",
                "position": "DIRECTOR",
                "can_sign": True,
                "phone": "+7 900 000-03-01",
            },
        ],
        "demo_request": {
            "product_code": "INVESTMENT",
            "purpose": "EQUIPMENT",
            "amount": 25_000_000,
            "term_months": 60,
            "purpose_details": "Покупка обрабатывающего центра с ЧПУ для участка мехобработки",
            "collateral_description": "Обрабатывающий центр с ЧПУ, поставщик ООО «СтанкоИмпорт» (синт.), стоимость 31 млн ₽",
        },
    },
    {
        "key": "stroy",
        "scenario": "Ручное рассмотрение: повышенный риск",
        "scenario_hint": "Оборотный кредит 8 млн ₽ на 18 мес. под контракт — налоговый долг и смена директора, решает аналитик",
        "inn": inn_legal(41207),
        "ogrn": ogrn(2022, 41207),
        "kind": "LEGAL",
        "short_name": "ООО «Стройресурс»",
        "full_name": "Общество с ограниченной ответственностью «Стройресурс»",
        "okved": {"code": "41.20", "name": "Строительство жилых и нежилых зданий"},
        "registration": {"months_ago": 47, "day": 10},
        "client_since": {"months_ago": 44, "day": 1},
        "address": "603022, Нижегородская обл., г. Нижний Новгород, ул. Строителей, д. 21, оф. 5",
        "segment": "Малое предприятие",
        "headcount": 35,
        "settlement_account": account("40702", 4004),
        "turnover": {
            "base": 5_200_000,
            "monthly_growth": 0.0,
            "noise": 0.22,
            "seed": 404,
            "months": 12,
            "debit_ratio": 0.98,
        },
        "egrul": {
            "status": "ACTIVE",
            "status_text": "Действующее",
            "director": {
                "name": "Никитин Роман Валерьевич",
                "position": "Генеральный директор",
                "since": {"months_ago": 2, "day": 5},
            },
            "previous_director": {"name": "Гусев Олег Петрович", "until": {"months_ago": 2, "day": 5}},
            "founders": [{"name": "Гусев Олег Петрович", "share": 100}],
            "authorized_capital": 10000,
        },
        "fns": {
            "tax_regime": "ОСН",
            "smb_category": "Малое предприятие",
            "avg_headcount": 35,
            "revenue_last_year": 61_000_000,
            "net_profit_last_year": 6_800_000,
            "tax_arrears": 420_000,
            "suspended_accounts": False,
            "mass_address": False,
            "unreliable_data": False,
            "disqualified_persons": False,
        },
        "bki": {
            "score": 611,
            "history_length_months": 40,
            "inquiries_30d": 2,
            "max_overdue_days_12m": 18,
            "max_overdue_days_ever": 18,
            "current_overdue_days": 0,
            "loans": [
                {
                    "creditor": "Банк «Волга» (синтетический)",
                    "type": "Кредитная линия",
                    "amount": 6_000_000,
                    "outstanding": 3_900_000,
                    "monthly_payment": 410_000,
                    "opened": {"months_ago": 15, "day": 11},
                    "status": "ACTIVE",
                    "max_overdue_days_12m": 18,
                },
                {
                    "creditor": "Банк «Ока» (синтетический)",
                    "type": "Кредит на спецтехнику",
                    "amount": 4_200_000,
                    "outstanding": 2_100_000,
                    "monthly_payment": 230_000,
                    "opened": {"months_ago": 22, "day": 2},
                    "status": "ACTIVE",
                    "max_overdue_days_12m": 0,
                },
            ],
        },
        "users": [
            {
                "login": "nikitin",
                "full_name": "Никитин Роман Валерьевич",
                "position": "DIRECTOR",
                "can_sign": True,
                "phone": "+7 900 000-04-01",
            },
        ],
        "demo_request": {
            "product_code": "WORKING_CAPITAL",
            "purpose": "CONTRACT",
            "amount": 8_000_000,
            "term_months": 18,
            "purpose_details": "Закупка материалов под муниципальный контракт на ремонт школы",
        },
    },
    {
        "key": "vector",
        "scenario": "Автоматический отказ",
        "scenario_hint": "Экспресс-кредит 2 млн ₽ — компания в стадии ликвидации, стоп-фактор",
        "inn": inn_legal(49410),
        "ogrn": ogrn(2015, 49410),
        "kind": "LEGAL",
        "short_name": "ООО «Вектор Логистик»",
        "full_name": "Общество с ограниченной ответственностью «Вектор Логистик»",
        "okved": {"code": "49.41", "name": "Деятельность автомобильного грузового транспорта"},
        "registration": {"months_ago": 137, "day": 20},
        "client_since": {"months_ago": 102, "day": 1},
        "address": "603140, Нижегородская обл., г. Нижний Новгород, ул. Складская, д. 3",
        "segment": "Малое предприятие",
        "headcount": 19,
        "settlement_account": account("40702", 5005),
        "turnover": {
            "base": 3_400_000,
            "monthly_growth": -0.05,
            "noise": 0.10,
            "seed": 505,
            "months": 12,
            "debit_ratio": 1.02,
        },
        "egrul": {
            "status": "LIQUIDATING",
            "status_text": "Находится в стадии ликвидации",
            "status_since": {"months_ago": 1, "day": 10},
            "director": {
                "name": "Фёдоров Антон Игоревич",
                "position": "Ликвидатор",
                "since": {"months_ago": 1, "day": 10},
            },
            "founders": [{"name": "Фёдоров Антон Игоревич", "share": 100}],
            "authorized_capital": 20000,
        },
        "fns": {
            "tax_regime": "ОСН",
            "smb_category": "Малое предприятие",
            "avg_headcount": 19,
            "revenue_last_year": 31_000_000,
            "net_profit_last_year": -2_400_000,
            "tax_arrears": 1_600_000,
            "suspended_accounts": False,
            "mass_address": False,
            "unreliable_data": False,
            "disqualified_persons": False,
        },
        "bki": {
            "score": 540,
            "history_length_months": 70,
            "inquiries_30d": 4,
            "max_overdue_days_12m": 25,
            "max_overdue_days_ever": 25,
            "current_overdue_days": 0,
            "loans": [],
        },
        "users": [
            {
                "login": "fedorov",
                "full_name": "Фёдоров Антон Игоревич",
                "position": "DIRECTOR",
                "can_sign": True,
                "phone": "+7 900 000-05-01",
            },
        ],
        "demo_request": {
            "product_code": "EXPRESS",
            "purpose": "WORKING_CAPITAL",
            "amount": 2_000_000,
            "term_months": 24,
            "purpose_details": "Оплата топлива и ремонта грузовиков",
        },
    },
    {
        "key": "coffee",
        "scenario": "Запрос уточнений → одобрение",
        "scenario_hint": "Овердрафт 350 тыс. ₽ — выручка в заявке больше оборотов по счёту, банк попросит выписку из другого банка",
        "inn": inn_person(563011),
        "ogrn": ogrnip(2025, 563011),
        "kind": "IP",
        "short_name": "ИП Морозов Д. И.",
        "full_name": "Индивидуальный предприниматель Морозов Дмитрий Игоревич",
        "okved": {"code": "56.30", "name": "Подача напитков"},
        "registration": {"months_ago": 11, "day": 18},
        "client_since": {"months_ago": 10, "day": 1},
        "address": "603001, Нижегородская обл., г. Нижний Новгород, ул. Набережная, д. 9",
        "segment": "Микропредприятие",
        "headcount": 5,
        "settlement_account": account("40802", 6006),
        "turnover": {
            "base": 560_000,
            "monthly_growth": 0.03,
            "noise": 0.04,
            "seed": 606,
            "months": 10,
            "debit_ratio": 0.96,
        },
        "egrul": {
            "status": "ACTIVE",
            "status_text": "Действующий ИП",
            "director": {
                "name": "Морозов Дмитрий Игоревич",
                "position": "Индивидуальный предприниматель",
                "since": {"months_ago": 11, "day": 18},
            },
            "founders": [],
            "authorized_capital": None,
        },
        "fns": {
            "tax_regime": "УСН «доходы»",
            "smb_category": "Микропредприятие",
            "avg_headcount": 5,
            "revenue_last_year": 650_000,
            "net_profit_last_year": None,
            "tax_arrears": 0,
            "suspended_accounts": False,
            "mass_address": False,
            "unreliable_data": False,
            "disqualified_persons": False,
        },
        "bki": {
            "score": None,
            "history_length_months": 0,
            "inquiries_30d": 1,
            "max_overdue_days_12m": 0,
            "max_overdue_days_ever": 0,
            "current_overdue_days": 0,
            "loans": [],
        },
        "users": [
            {
                "login": "morozov",
                "full_name": "Морозов Дмитрий Игоревич",
                "position": "OWNER",
                "can_sign": True,
                "phone": "+7 900 000-06-01",
            },
        ],
        "demo_request": {
            "product_code": "OVERDRAFT",
            "purpose": "CASH_GAP",
            "amount": 350_000,
            "term_months": 12,
            "purpose_details": "Подстраховка на время поставок зерна и молока",
            "declared_revenue": 14_400_000,
        },
    },
]

STAFF = [
    {
        "login": "analyst",
        "full_name": "Волкова Ирина Андреевна",
        "role": "ANALYST",
        "title": "Кредитный аналитик (андеррайтер)",
    },
    {
        "login": "risk",
        "full_name": "Белов Сергей Николаевич",
        "role": "RISK_MANAGER",
        "title": "Риск-менеджер: параметры политики",
    },
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "_comment": "Синтетические данные. Сгенерировано tools/generate_synthetic_data.py — не редактируйте вручную.",
        "demo_password": "demo1234",
        "companies": COMPANIES,
        "staff": STAFF,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for c in COMPANIES:
        print(f"{c['key']:<11} {c['inn']:<13} {c['ogrn']:<16} {c['short_name']}")


if __name__ == "__main__":
    main()
