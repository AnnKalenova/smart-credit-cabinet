from types import SimpleNamespace

import pytest

from app.domain.checks import precheck
from app.domain.enums import TERMINAL_STATUSES, AppStatus, ConsentType
from app.domain.products import PRODUCTS_SEED, required_documents
from app.domain.statuses import (
    BANK_LABELS,
    CLIENT_STATUS,
    STEP_OF_STATUS,
    TRANSITIONS,
    TransitionError,
    assert_transition,
    client_view,
)


def _product(code: str) -> SimpleNamespace:
    spec = next(p for p in PRODUCTS_SEED if p["code"] == code)
    return SimpleNamespace(
        **{
            **spec,
            "purposes": [str(x) for x in spec["purposes"]],
            "doc_rules": [
                {
                    **r,
                    "doc_type": str(r["doc_type"]),
                    "value": str(r["value"]) if r.get("when") == "purpose" else r.get("value"),
                }
                for r in spec["doc_rules"]
            ],
            "is_active": True,
        }
    )


# --------------------------------------------------------------------------- машина состояний


def test_every_status_is_described_everywhere():
    for s in AppStatus:
        assert s in TRANSITIONS and s in CLIENT_STATUS and s in BANK_LABELS and s in STEP_OF_STATUS


def test_terminal_statuses_have_no_exits():
    for s in TERMINAL_STATUSES:
        assert TRANSITIONS[s] == set()


@pytest.mark.parametrize(
    "src,dst",
    [
        ("DRAFT", "SUBMITTED"),
        ("SUBMITTED", "PRECHECK"),
        ("PRECHECK", "NEEDS_INFO"),
        ("NEEDS_INFO", "PRECHECK"),
        ("DECISION", "MANUAL_REVIEW"),
        ("MANUAL_REVIEW", "ENRICHMENT"),
        ("OFFER_READY", "SIGNING"),
        ("SIGNING", "OFFER_READY"),
        ("SIGNING", "EXPIRED"),
        ("DISBURSEMENT", "ISSUED"),
    ],
)
def test_allowed_transitions(src, dst):
    assert_transition(src, dst)


@pytest.mark.parametrize(
    "src,dst",
    [
        ("DRAFT", "ISSUED"),
        ("PRECHECK", "OFFER_READY"),
        ("OFFER_READY", "ISSUED"),
        ("ISSUED", "CANCELLED"),
        ("DECLINED", "MANUAL_REVIEW"),
        ("SIGNING", "CANCELLED"),
    ],
)
def test_forbidden_transitions(src, dst):
    with pytest.raises(TransitionError):
        assert_transition(src, dst)


def test_client_view_tones_and_steps():
    assert client_view("NEEDS_INFO")["tone"] == "action"
    assert client_view("ENRICHMENT")["tone"] == "progress"
    assert client_view("ISSUED")["tone"] == "positive"
    assert client_view("DECLINED")["tone"] == "negative"
    # отзыв заявки и отказ от предложения — решение клиента, не отказ банка
    assert client_view("CANCELLED")["tone"] == "neutral"
    assert client_view("CLIENT_DECLINED")["tone"] == "neutral"
    assert client_view("DRAFT")["step"] == 0
    assert client_view("OFFER_READY")["step"] == 2
    assert client_view("ISSUED")["is_final"] is True
    assert client_view("MANUAL_REVIEW")["is_final"] is False
    assert len(client_view("DRAFT")["steps"]) == 5


# --------------------------------------------------------------------------- проверка полноты


def _app(**overrides) -> SimpleNamespace:
    base = {
        "amount": 25_000_000,
        "term_months": 60,
        "purpose": "EQUIPMENT",
        "declared_revenue": 228_000_000,
        "contact_name": "Лебедев И. О.",
        "contact_phone": "+7 900 000-03-01",
        "has_collateral": False,
        "collateral_description": "Обрабатывающий центр с ЧПУ",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


COMPANY = SimpleNamespace(inn="0000284112", kind="LEGAL")
CONSENTS = [
    SimpleNamespace(consent_type=ConsentType.BKI_REQUEST),
    SimpleNamespace(consent_type=ConsentType.PERSONAL_DATA),
]
DOCS = [SimpleNamespace(doc_type="SUPPLIER_INVOICE"), SimpleNamespace(doc_type="FIN_STATEMENTS")]


def _bad(checks) -> dict[str, str]:
    return {c.code: c.severity for c in checks if c.severity != "OK"}


def test_precheck_passes_complete_application():
    checks = precheck(_app(), _product("INVESTMENT"), COMPANY, DOCS, CONSENTS)
    assert _bad(checks) == {}


def test_precheck_reports_missing_documents_fields_and_consents():
    checks = precheck(
        _app(collateral_description="", declared_revenue=None, contact_phone=None),
        _product("INVESTMENT"),
        COMPANY,
        [],
        [],
    )
    bad = _bad(checks)
    assert bad["doc_supplier_invoice"] == "MISSING"
    assert bad["doc_fin_statements"] == "MISSING"
    assert bad["collateral_description"] == "MISSING"
    assert bad["revenue_present"] == "MISSING"
    assert bad["contact_present"] == "MISSING"
    assert bad["consent_bki_request"] == "STOP"
    assert bad["consent_personal_data"] == "STOP"
    missing_docs = [c.info_item for c in checks if c.code.startswith("doc_") and c.severity == "MISSING"]
    assert all(item["type"] == "DOCUMENT" for item in missing_docs)


def test_precheck_range_purpose_and_inn():
    checks = precheck(
        _app(amount=500, term_months=200, purpose="CASH_GAP"),
        _product("INVESTMENT"),
        SimpleNamespace(inn="0000284113", kind="LEGAL"),
        DOCS,
        CONSENTS,
    )
    bad = _bad(checks)
    assert bad["amount_range"] == "MISSING"
    assert bad["term_range"] == "MISSING"
    assert bad["purpose_allowed"] == "MISSING"
    assert bad["inn_valid"] == "STOP"


def test_required_documents_rules():
    wc = _product("WORKING_CAPITAL")
    assert required_documents(wc, 5_000_000, "WORKING_CAPITAL", "LEGAL") == []
    assert required_documents(wc, 12_000_000, "WORKING_CAPITAL", "LEGAL") == ["FIN_STATEMENTS"]
    assert required_documents(wc, 12_000_000, "WORKING_CAPITAL", "IP") == ["TAX_RETURN"]
    assert required_documents(wc, 1_000_000, "CONTRACT", "LEGAL") == ["CONTRACT_COPY"]
    assert required_documents(_product("INVESTMENT"), 2_000_000, "EQUIPMENT", "IP") == ["SUPPLIER_INVOICE"]
    assert required_documents(_product("EXPRESS"), 5_000_000, "OTHER", "LEGAL") == []


def test_precheck_requires_description_of_offered_collateral():
    wc = _product("WORKING_CAPITAL")
    app = _app(
        amount=3_000_000, term_months=12, purpose="WORKING_CAPITAL", has_collateral=True, collateral_description=" "
    )
    check = next(c for c in precheck(app, wc, COMPANY, [], CONSENTS) if c.code == "collateral_description")
    assert check.severity == "MISSING"
    assert check.info_item["label"] == "Опишите имущество, которое предлагаете в залог"
    app.has_collateral = False
    assert "collateral_description" not in _bad(precheck(app, wc, COMPANY, [], CONSENTS))
