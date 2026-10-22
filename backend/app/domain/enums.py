"""Справочники и перечисления предметной области."""

from enum import StrEnum


class Role(StrEnum):
    CLIENT = "CLIENT"  # представитель компании-клиента
    ANALYST = "ANALYST"  # кредитный аналитик (андеррайтер)
    RISK_MANAGER = "RISK_MANAGER"  # риск-менеджер: параметры кредитной политики


class CompanyKind(StrEnum):
    LEGAL = "LEGAL"  # юридическое лицо
    IP = "IP"  # индивидуальный предприниматель


class MemberPosition(StrEnum):
    OWNER = "OWNER"
    DIRECTOR = "DIRECTOR"
    CFO = "CFO"
    ACCOUNTANT = "ACCOUNTANT"
    EMPLOYEE = "EMPLOYEE"


POSITION_LABELS = {
    MemberPosition.OWNER: "Собственник",
    MemberPosition.DIRECTOR: "Генеральный директор",
    MemberPosition.CFO: "Финансовый директор",
    MemberPosition.ACCOUNTANT: "Главный бухгалтер",
    MemberPosition.EMPLOYEE: "Сотрудник по доверенности",
}


class AppStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PRECHECK = "PRECHECK"
    NEEDS_INFO = "NEEDS_INFO"
    ENRICHMENT = "ENRICHMENT"
    SCORING = "SCORING"
    DECISION = "DECISION"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    OFFER_READY = "OFFER_READY"
    SIGNING = "SIGNING"
    DISBURSEMENT = "DISBURSEMENT"
    ISSUED = "ISSUED"
    DECLINED = "DECLINED"
    CLIENT_DECLINED = "CLIENT_DECLINED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


TERMINAL_STATUSES = {
    AppStatus.ISSUED,
    AppStatus.DECLINED,
    AppStatus.CLIENT_DECLINED,
    AppStatus.CANCELLED,
    AppStatus.EXPIRED,
}


class Purpose(StrEnum):
    WORKING_CAPITAL = "WORKING_CAPITAL"
    CASH_GAP = "CASH_GAP"
    EQUIPMENT = "EQUIPMENT"
    VEHICLE = "VEHICLE"
    EXPANSION = "EXPANSION"
    CONTRACT = "CONTRACT"
    REFINANCING = "REFINANCING"
    OTHER = "OTHER"


PURPOSES = {
    Purpose.WORKING_CAPITAL: {
        "label": "Закупка товара, сырья, материалов",
        "hint": "Пополнить оборотные средства под сезон или крупный заказ",
        "icon": "package",
    },
    Purpose.CASH_GAP: {
        "label": "Закрыть кассовый разрыв",
        "hint": "Покупатели платят позже, а платить поставщикам и зарплату нужно сейчас",
        "icon": "clock",
    },
    Purpose.EQUIPMENT: {
        "label": "Купить оборудование",
        "hint": "Станки, производственные линии, торговое и кухонное оборудование",
        "icon": "cog",
    },
    Purpose.VEHICLE: {
        "label": "Купить транспорт или спецтехнику",
        "hint": "Грузовики, фургоны, спецтехника для бизнеса",
        "icon": "truck",
    },
    Purpose.EXPANSION: {
        "label": "Открыть точку или сделать ремонт",
        "hint": "Новый филиал, ремонт помещения, расширение производства",
        "icon": "store",
    },
    Purpose.CONTRACT: {
        "label": "Исполнить контракт или тендер",
        "hint": "Профинансировать заказ до оплаты от заказчика",
        "icon": "file",
    },
    Purpose.REFINANCING: {
        "label": "Рефинансировать кредит",
        "hint": "Перевести кредит из другого банка на более удобные условия",
        "icon": "refresh",
    },
    Purpose.OTHER: {
        "label": "Другие цели бизнеса",
        "hint": "Любые задачи бизнеса, кроме личных",
        "icon": "briefcase",
    },
}


class DocType(StrEnum):
    FIN_STATEMENTS = "FIN_STATEMENTS"
    TAX_RETURN = "TAX_RETURN"
    SUPPLIER_INVOICE = "SUPPLIER_INVOICE"
    CONTRACT_COPY = "CONTRACT_COPY"
    BANK_STATEMENT = "BANK_STATEMENT"
    EXPLANATION = "EXPLANATION"
    OTHER = "OTHER"


DOC_TYPES = {
    DocType.FIN_STATEMENTS: "Бухгалтерская отчётность за последний год",
    DocType.TAX_RETURN: "Налоговая декларация за последний год",
    DocType.SUPPLIER_INVOICE: "Счёт или спецификация поставщика",
    DocType.CONTRACT_COPY: "Контракт с заказчиком",
    DocType.BANK_STATEMENT: "Выписка по счёту в другом банке",
    DocType.EXPLANATION: "Пояснение клиента",
    DocType.OTHER: "Другой документ",
}


class JobStage(StrEnum):
    PRECHECK = "PRECHECK"
    ENRICHMENT = "ENRICHMENT"
    SCORING = "SCORING"
    DECISION = "DECISION"
    DISBURSEMENT = "DISBURSEMENT"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class DecisionOutcome(StrEnum):
    APPROVE = "APPROVE"
    COUNTER_OFFER = "COUNTER_OFFER"
    NEEDS_INFO = "NEEDS_INFO"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    DECLINE = "DECLINE"


DECISION_LABELS = {
    DecisionOutcome.APPROVE: "Одобрено",
    DecisionOutcome.COUNTER_OFFER: "Встречное предложение",
    DecisionOutcome.NEEDS_INFO: "Запрос информации",
    DecisionOutcome.MANUAL_REVIEW: "Передано на ручное рассмотрение",
    DecisionOutcome.DECLINE: "Отказ",
}


class DecisionKind(StrEnum):
    AUTO = "AUTO"
    MANUAL = "MANUAL"


class OfferStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class ActorType(StrEnum):
    CLIENT = "CLIENT"
    ANALYST = "ANALYST"
    SYSTEM = "SYSTEM"


class SignPurpose(StrEnum):
    CONSENTS = "CONSENTS"
    CONTRACT = "CONTRACT"


class InfoRequestStatus(StrEnum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    CANCELLED = "CANCELLED"


class ConsentType(StrEnum):
    BKI_REQUEST = "BKI_REQUEST"
    PERSONAL_DATA = "PERSONAL_DATA"


CONSENT_TEXTS = {
    ConsentType.BKI_REQUEST: (
        "Согласие на запрос кредитной истории в бюро кредитных историй "
        "для оценки заявки на кредит (ст. 6 218-ФЗ «О кредитных историях»)"
    ),
    ConsentType.PERSONAL_DATA: (
        "Согласие на обработку персональных данных руководителя и учредителей "
        "в целях рассмотрения заявки (152-ФЗ «О персональных данных»)"
    ),
}
