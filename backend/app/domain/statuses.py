"""Машина состояний заявки и понятные клиенту формулировки статусов."""

from app.domain.enums import AppStatus as S

TRANSITIONS: dict[S, set[S]] = {
    S.DRAFT: {S.SUBMITTED, S.CANCELLED},
    S.SUBMITTED: {S.PRECHECK, S.CANCELLED},
    S.PRECHECK: {S.ENRICHMENT, S.NEEDS_INFO, S.CANCELLED},
    S.NEEDS_INFO: {S.PRECHECK, S.CANCELLED},
    S.ENRICHMENT: {S.SCORING, S.MANUAL_REVIEW, S.CANCELLED},
    S.SCORING: {S.DECISION, S.CANCELLED},
    S.DECISION: {S.OFFER_READY, S.NEEDS_INFO, S.MANUAL_REVIEW, S.DECLINED, S.CANCELLED},
    S.MANUAL_REVIEW: {S.OFFER_READY, S.NEEDS_INFO, S.DECLINED, S.CANCELLED, S.ENRICHMENT},
    S.OFFER_READY: {S.SIGNING, S.CLIENT_DECLINED, S.EXPIRED},
    S.SIGNING: {S.DISBURSEMENT, S.OFFER_READY},
    S.DISBURSEMENT: {S.ISSUED},
    S.ISSUED: set(),
    S.DECLINED: set(),
    S.CLIENT_DECLINED: set(),
    S.CANCELLED: set(),
    S.EXPIRED: set(),
}

# Клиент может отозвать заявку, пока по ней не принято решение
CLIENT_CANCELLABLE = {
    S.DRAFT,
    S.SUBMITTED,
    S.PRECHECK,
    S.NEEDS_INFO,
    S.ENRICHMENT,
    S.SCORING,
    S.DECISION,
    S.MANUAL_REVIEW,
}


class TransitionError(Exception):
    pass


def assert_transition(current: str, target: str) -> None:
    if S(target) not in TRANSITIONS[S(current)]:
        raise TransitionError(f"Переход {current} → {target} не разрешён")


# Для банка: короткое название статуса
BANK_LABELS = {
    S.DRAFT: "Черновик",
    S.SUBMITTED: "Подана",
    S.PRECHECK: "Проверка полноты",
    S.NEEDS_INFO: "Запрошена информация",
    S.ENRICHMENT: "Сбор данных",
    S.SCORING: "Скоринг",
    S.DECISION: "Решение",
    S.MANUAL_REVIEW: "Ручное рассмотрение",
    S.OFFER_READY: "Предложение у клиента",
    S.SIGNING: "Подписание",
    S.DISBURSEMENT: "Выдача",
    S.ISSUED: "Выдан",
    S.DECLINED: "Отказ",
    S.CLIENT_DECLINED: "Отказ клиента",
    S.CANCELLED: "Отменена",
    S.EXPIRED: "Предложение истекло",
}

# Для клиента: название, что происходит и что делать дальше
CLIENT_STATUS = {
    S.DRAFT: ("Черновик", "Заявка сохранена, но ещё не отправлена в банк.", "Проверьте данные и отправьте заявку."),
    S.SUBMITTED: ("Отправлена", "Заявка принята банком.", "Ничего делать не нужно."),
    S.PRECHECK: (
        "Проверяем данные",
        "Проверяем, что данные и документы заполнены полностью и без ошибок.",
        "Ничего делать не нужно — это займёт несколько секунд.",
    ),
    S.NEEDS_INFO: (
        "Нужны уточнения",
        "Чтобы продолжить рассмотрение, банку нужна дополнительная информация.",
        "Ответьте на запрос ниже — после ответа заявка продолжит движение автоматически.",
    ),
    S.ENRICHMENT: (
        "Собираем сведения",
        "Запрашиваем сведения о компании в ЕГРЮЛ, ФНС и бюро кредитных историй.",
        "Ничего делать не нужно — обычно это занимает меньше минуты.",
    ),
    S.SCORING: (
        "Оцениваем заявку",
        "Рассчитываем кредитный рейтинг по данным о компании и обороте по счёту.",
        "Ничего делать не нужно.",
    ),
    S.DECISION: ("Готовим решение", "Подбираем сумму, срок и ставку.", "Ничего делать не нужно."),
    S.MANUAL_REVIEW: (
        "У кредитного специалиста",
        "Заявку изучает кредитный специалист банка.",
        "Решение появится здесь же, в кабинете. Если понадобятся документы, мы напишем в этой заявке.",
    ),
    S.OFFER_READY: (
        "Готово предложение",
        "Банк подготовил условия кредита.",
        "Ознакомьтесь с условиями и примите предложение, пока оно действует.",
    ),
    S.SIGNING: (
        "Подписание договора",
        "Кредитный договор и график платежей сформированы.",
        "Подпишите документы кодом подтверждения. Подписывает руководитель или представитель по доверенности.",
    ),
    S.DISBURSEMENT: (
        "Перечисляем деньги",
        "Открываем ссудный счёт и переводим деньги на ваш расчётный счёт.",
        "Ничего делать не нужно — обычно это занимает меньше минуты.",
    ),
    S.ISSUED: (
        "Кредит выдан",
        "Деньги зачислены на расчётный счёт.",
        "График платежей доступен в разделе «Мои кредиты».",
    ),
    S.DECLINED: (
        "Отказ",
        "Банк не может выдать кредит на этих условиях.",
        "Причины указаны ниже. Вы можете подать новую заявку, когда ситуация изменится.",
    ),
    S.CLIENT_DECLINED: (
        "Вы отказались от предложения",
        "Заявка закрыта по вашему решению.",
        "Можно подать новую заявку в любой момент — данные заполнятся автоматически.",
    ),
    S.CANCELLED: ("Заявка отозвана", "Заявка закрыта по вашему решению.", "Можно подать новую заявку в любой момент."),
    S.EXPIRED: (
        "Срок предложения истёк",
        "Предложение не было принято вовремя.",
        "Подайте новую заявку — данные заполнятся автоматически.",
    ),
}

CLIENT_STEPS = ["Заявка", "Проверка и оценка", "Предложение", "Подписание", "Выдача"]

STEP_OF_STATUS = {
    S.DRAFT: 0,
    S.SUBMITTED: 1,
    S.PRECHECK: 1,
    S.NEEDS_INFO: 1,
    S.ENRICHMENT: 1,
    S.SCORING: 1,
    S.DECISION: 1,
    S.MANUAL_REVIEW: 1,
    S.OFFER_READY: 2,
    S.SIGNING: 3,
    S.DISBURSEMENT: 4,
    S.ISSUED: 4,
    S.DECLINED: 1,
    S.CLIENT_DECLINED: 2,
    S.CANCELLED: 1,
    S.EXPIRED: 2,
}


def client_view(status: str) -> dict:
    s = S(status)
    title, now, nxt = CLIENT_STATUS[s]
    if s in (S.DECLINED, S.CLIENT_DECLINED, S.CANCELLED, S.EXPIRED):
        tone = "negative"
    elif s in (S.NEEDS_INFO, S.OFFER_READY, S.SIGNING, S.DRAFT):
        tone = "action"
    elif s == S.ISSUED:
        tone = "positive"
    else:
        tone = "progress"
    return {
        "title": title,
        "now": now,
        "next": nxt,
        "tone": tone,
        "step": STEP_OF_STATUS[s],
        "steps": CLIENT_STEPS,
        "is_final": not TRANSITIONS[s],
    }
