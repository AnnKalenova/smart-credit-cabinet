"""Проверка полноты и корректности заявки (этап PRECHECK)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.domain.enums import DOC_TYPES, PURPOSES, ConsentType
from app.domain.finance import inn_is_valid
from app.domain.products import pledged, required_documents
from app.domain.text import fmt_amount, fmt_money, months_range


@dataclass
class Check:
    code: str
    severity: str  # OK / MISSING / STOP
    message: str
    info_item: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def precheck(app, product, company, documents: list, consents: list) -> list[Check]:
    checks: list[Check] = []
    amount = float(app.amount)

    checks.append(
        Check("inn_valid", "OK", f"ИНН {company.inn} прошёл проверку контрольных цифр")
        if inn_is_valid(company.inn)
        else Check("inn_valid", "STOP", f"ИНН {company.inn} не прошёл проверку контрольных цифр")
    )

    if not product.is_active:
        checks.append(Check("product_active", "STOP", f"Продукт «{product.name}» сейчас недоступен"))

    if float(product.min_amount) <= amount <= float(product.max_amount):
        checks.append(Check("amount_range", "OK", "Сумма в пределах условий продукта"))
    else:
        checks.append(
            Check(
                "amount_range",
                "MISSING",
                f"Сумма должна быть от {fmt_amount(product.min_amount)} до {fmt_money(product.max_amount)}",
                {
                    "code": "amount",
                    "type": "FIELD",
                    "field": "amount",
                    "label": f"Укажите сумму от {fmt_amount(product.min_amount)} до {fmt_money(product.max_amount)}",
                },
            )
        )

    if product.min_term <= app.term_months <= product.max_term:
        checks.append(Check("term_range", "OK", "Срок в пределах условий продукта"))
    else:
        checks.append(
            Check(
                "term_range",
                "MISSING",
                f"Срок по продукту — {months_range(product.min_term, product.max_term)}",
                {
                    "code": "term",
                    "type": "FIELD",
                    "field": "term_months",
                    "label": f"Укажите срок: {months_range(product.min_term, product.max_term)}",
                },
            )
        )

    if app.purpose in product.purposes:
        checks.append(Check("purpose_allowed", "OK", f"Цель «{PURPOSES[app.purpose]['label']}» подходит для продукта"))
    else:
        checks.append(
            Check(
                "purpose_allowed",
                "MISSING",
                "Цель кредита не подходит для выбранного продукта",
                {
                    "code": "purpose",
                    "type": "FIELD",
                    "field": "purpose",
                    "label": "Выберите подходящую цель или другой продукт",
                },
            )
        )

    if app.declared_revenue and float(app.declared_revenue) > 0:
        checks.append(Check("revenue_present", "OK", "Указана годовая выручка"))
    else:
        checks.append(
            Check(
                "revenue_present",
                "MISSING",
                "Не указана годовая выручка",
                {
                    "code": "declared_revenue",
                    "type": "FIELD",
                    "field": "declared_revenue",
                    "label": "Укажите выручку за последние 12 месяцев",
                },
            )
        )

    if app.contact_name and app.contact_phone:
        checks.append(Check("contact_present", "OK", "Указано контактное лицо"))
    else:
        checks.append(
            Check(
                "contact_present",
                "MISSING",
                "Не указано контактное лицо",
                {
                    "code": "contact",
                    "type": "FIELD",
                    "field": "contact_name",
                    "label": "Укажите контактное лицо и телефон",
                },
            )
        )

    if pledged(product, app.has_collateral) and not (app.collateral_description or "").strip():
        checks.append(
            Check(
                "collateral_description",
                "MISSING",
                "Не описано имущество, которое будет в залоге",
                {
                    "code": "collateral",
                    "type": "FIELD",
                    "field": "collateral_description",
                    "label": "Опишите приобретаемое имущество (что, у кого, стоимость)"
                    if product.collateral == "REQUIRED"
                    else "Опишите имущество, которое предлагаете в залог",
                },
            )
        )

    signed = {c.consent_type for c in consents}
    for ct in (ConsentType.BKI_REQUEST, ConsentType.PERSONAL_DATA):
        if ct not in signed:
            checks.append(Check(f"consent_{ct.lower()}", "STOP", "Не подписано обязательное согласие"))

    have = {d.doc_type for d in documents}
    for doc_type in required_documents(product, amount, app.purpose, company.kind):
        if doc_type in have:
            checks.append(Check(f"doc_{doc_type.lower()}", "OK", f"Приложен документ: {DOC_TYPES[doc_type]}"))
        else:
            checks.append(
                Check(
                    f"doc_{doc_type.lower()}",
                    "MISSING",
                    f"Не приложен документ: {DOC_TYPES[doc_type]}",
                    {
                        "code": f"doc_{doc_type.lower()}",
                        "type": "DOCUMENT",
                        "doc_type": doc_type,
                        "label": DOC_TYPES[doc_type],
                    },
                )
            )
    return checks
