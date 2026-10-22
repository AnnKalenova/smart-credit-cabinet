"""Линейка кредитных продуктов (собственная, не продукты Альфа-Банка).

Ставка = ключевая ставка Банка России + базовая надбавка продукта + надбавка за риск − скидки.
Значения откалиброваны под рыночную логику при ключевой ставке 14%: итоговые ставки 16–25% годовых.
"""

from app.domain.enums import DocType, Purpose

PRODUCTS_SEED: list[dict] = [
    {
        "code": "EXPRESS",
        "name": "Экспресс-кредит",
        "tagline": "Без залога и документов, решение за несколько минут",
        "description": (
            "Беззалоговый кредит на любые цели бизнеса. Решение принимается автоматически "
            "по данным о вашем расчётном счёте в банке — справки и отчётность не нужны."
        ),
        "purposes": [
            Purpose.WORKING_CAPITAL,
            Purpose.CASH_GAP,
            Purpose.EQUIPMENT,
            Purpose.EXPANSION,
            Purpose.OTHER,
        ],
        "min_amount": 300_000,
        "max_amount": 5_000_000,
        "min_term": 3,
        "max_term": 36,
        "base_spread": 7.0,
        "max_spread": 13.0,
        "turnover_multiplier": 1.5,
        "auto_decision_limit": 5_000_000,
        "repayment_type": "ANNUITY",
        "collateral": "NONE",
        "doc_rules": [],
        "features": ["До 5 млн ₽", "До 3 лет", "Без залога", "Без документов"],
        "sort_order": 1,
    },
    {
        "code": "WORKING_CAPITAL",
        "name": "Оборотный кредит",
        "tagline": "Закупка товара, сырья, исполнение контрактов",
        "description": (
            "Пополнение оборотных средств: закупка товара и сырья, исполнение контрактов, "
            "рефинансирование. Сумма зависит от оборотов бизнеса."
        ),
        "purposes": [Purpose.WORKING_CAPITAL, Purpose.CONTRACT, Purpose.REFINANCING, Purpose.CASH_GAP],
        "min_amount": 500_000,
        "max_amount": 30_000_000,
        "min_term": 3,
        "max_term": 24,
        "base_spread": 5.0,
        "max_spread": 11.0,
        "turnover_multiplier": 3.0,
        "auto_decision_limit": 10_000_000,
        "repayment_type": "ANNUITY",
        "collateral": "OPTIONAL",
        "doc_rules": [
            {"doc_type": DocType.FIN_STATEMENTS, "when": "amount_gt", "value": 10_000_000, "kinds": ["LEGAL"]},
            {"doc_type": DocType.TAX_RETURN, "when": "amount_gt", "value": 10_000_000, "kinds": ["IP"]},
            {"doc_type": DocType.CONTRACT_COPY, "when": "purpose", "value": Purpose.CONTRACT},
        ],
        "features": ["До 30 млн ₽", "До 2 лет", "Можно без залога", "Скидка за поручительство"],
        "sort_order": 2,
    },
    {
        "code": "INVESTMENT",
        "name": "Инвестиционный кредит",
        "tagline": "Оборудование, транспорт, новая точка",
        "description": (
            "Долгосрочный кредит на развитие: покупка оборудования и транспорта, ремонт и открытие "
            "новых точек. Приобретаемое имущество оформляется в залог — это снижает ставку."
        ),
        "purposes": [Purpose.EQUIPMENT, Purpose.VEHICLE, Purpose.EXPANSION],
        "min_amount": 1_000_000,
        "max_amount": 100_000_000,
        "min_term": 12,
        "max_term": 84,
        "base_spread": 4.0,
        "max_spread": 10.0,
        "turnover_multiplier": 6.0,
        "auto_decision_limit": 15_000_000,
        "repayment_type": "ANNUITY",
        "collateral": "REQUIRED",
        "doc_rules": [
            {"doc_type": DocType.SUPPLIER_INVOICE, "when": "always"},
            {"doc_type": DocType.FIN_STATEMENTS, "when": "amount_gt", "value": 10_000_000, "kinds": ["LEGAL"]},
            {"doc_type": DocType.TAX_RETURN, "when": "amount_gt", "value": 10_000_000, "kinds": ["IP"]},
        ],
        "features": ["До 100 млн ₽", "До 7 лет", "Залог — покупаемое имущество", "Самая низкая ставка"],
        "sort_order": 3,
    },
    {
        "code": "OVERDRAFT",
        "name": "Овердрафт",
        "tagline": "Подстраховка от кассовых разрывов",
        "description": (
            "Лимит на расчётном счёте: платежи проходят, даже если денег на счёте не хватает. "
            "Проценты начисляются только на использованную сумму, долг гасится поступлениями на счёт."
        ),
        "purposes": [Purpose.CASH_GAP, Purpose.WORKING_CAPITAL],
        "min_amount": 100_000,
        "max_amount": 10_000_000,
        "min_term": 12,
        "max_term": 12,
        "base_spread": 5.5,
        "max_spread": 11.5,
        "turnover_multiplier": 0.5,
        "auto_decision_limit": 10_000_000,
        "repayment_type": "REVOLVING",
        "collateral": "NONE",
        "doc_rules": [],
        "features": ["До 50% месячных поступлений", "На 12 месяцев", "Проценты только за использование"],
        "sort_order": 4,
    },
]

# Удобные сроки для подбора встречного предложения
NICE_TERMS = [3, 6, 9, 12, 18, 24, 30, 36, 48, 60, 72, 84]


def required_documents(product, amount: float, purpose: str, company_kind: str) -> list[str]:
    """Какие документы нужны для заявки с такими параметрами."""
    result: list[str] = []
    for rule in product.doc_rules:
        when = rule.get("when")
        kinds = rule.get("kinds")
        if kinds and company_kind not in kinds:
            continue
        if (
            when == "always"
            or (when == "amount_gt" and amount > float(rule["value"]))
            or (when == "purpose" and purpose == rule["value"])
        ):
            result.append(rule["doc_type"])
    return result
