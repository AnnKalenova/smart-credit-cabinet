"""API кредитного конвейера «Умный кредитный кабинет предпринимателя»."""

import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from sqlalchemy.exc import DataError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import applications, auth, bank, client, system
from app.config import get_settings
from app.domain.statuses import TransitionError
from app.logging_setup import setup_logging

settings = get_settings()
log = logging.getLogger("api")

REQUESTS = Counter("http_requests_total", "HTTP-запросы", ["method", "route", "status"])
LATENCY = Histogram("http_request_duration_seconds", "Длительность HTTP-запросов", ["method", "route"])

DEFAULT_SECRETS = {"dev-only-secret-change-me-0123456789abcdef", "local-demo-secret-change-me-0123456789abcdef"}

DESCRIPTION = """
Цифровой кредитный конвейер для малого и микробизнеса.

**Клиентский путь:** подбор продукта → черновик заявки с предзаполненными данными → согласия
(простая ЭП кодом внутри продукта) → отправка → уточнения → предложение → подписание → выдача.

**Банковская обработка:** проверка полноты → сбор данных (ЕГРЮЛ/ЕГРИП, ФНС, БКИ — симуляторы) →
скоринг (балльная модель) → кредитная политика → автоматическое решение или ручное рассмотрение.

**Конструктор условий:** `POST /api/v1/applications/{id}/simulation` — клиент меняет сумму, срок, продукт,
залог и поручительство и сразу видит решение банка, ставку и платёж, а при отказе — что изменить.

Авторизация: `POST /api/v1/auth/login` → кнопка **Authorize** → `Bearer <access_token>`.
Демо-учётки: `GET /api/v1/auth/demo-accounts`, пароль у всех `demo1234`.
"""


def _disable_demo_users() -> None:
    """База могла остаться с демо-стенда: учётки с общеизвестным паролем вне демо-режима не работают."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models import User
    from app.seed import DEMO_ACCOUNTS

    logins = [a["login"] for a in DEMO_ACCOUNTS]
    if not logins:
        return
    try:
        with SessionLocal() as db:
            result = db.execute(
                update(User).where(User.login.in_(logins), User.is_active.is_(True)).values(is_active=False)
            )
            db.commit()
        if result.rowcount:
            log.warning("демо-режим выключен: демо-учётки отключены", extra={"count": result.rowcount})
    except Exception:  # noqa: BLE001 — например, схема ещё не создана: старт API это не блокирует
        log.exception("не удалось отключить демо-учётки")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if settings.jwt_secret in DEFAULT_SECRETS:
        if not settings.demo_mode:
            # вне демо-режима сервис не стартует с общеизвестным секретом подписи токенов
            raise RuntimeError("Задайте собственный JWT_SECRET: значение по умолчанию допустимо только в демо-режиме")
        log.warning("используется демо-значение JWT_SECRET — смените его вне демо-стенда")
    if not settings.demo_mode:
        _disable_demo_users()
    log.info("API запущен", extra={"environment": settings.environment})
    yield


app = FastAPI(
    title="Умный кредитный кабинет предпринимателя — API",
    version="1.1.0",
    description=DESCRIPTION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@app.middleware("http")
async def observe(request: Request, call_next):
    # идентификатор от прокси принимаем, только если он похож на идентификатор, — иначе генерируем свой
    incoming = request.headers.get("X-Request-ID", "")
    request_id = incoming if REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Непредвиденная ошибка: структурированный лог с request_id и понятный ответ вместо трейсбека
        log.exception(
            "необработанная ошибка",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path},
        )
        response = JSONResponse(
            status_code=500,
            content={"detail": "Внутренняя ошибка сервиса. Попробуйте ещё раз", "request_id": request_id},
        )
    elapsed = time.perf_counter() - started
    route = request.scope.get("route")
    route_path = getattr(route, "path", "unmatched")
    REQUESTS.labels(request.method, route_path, response.status_code).inc()
    LATENCY.labels(request.method, route_path).observe(elapsed)
    response.headers["X-Request-ID"] = request_id
    if not route_path.startswith(("/health", "/metrics")):
        log.info(
            "запрос",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": route_path,
                "status": response.status_code,
                "ms": int(elapsed * 1000),
            },
        )
    return response


FIELD_LABELS = {
    "amount": "Сумма",
    "term_months": "Срок",
    "purpose": "Цель",
    "product_code": "Продукт",
    "declared_revenue": "Выручка",
    "contact_name": "Контактное лицо",
    "contact_phone": "Телефон",
    "collateral_description": "Описание залога",
    "purpose_details": "Описание задачи",
    "login": "Логин",
    "password": "Пароль",
    "text": "Сообщение",
    "code": "Код подтверждения",
    "challenge_id": "Код подтверждения",
    "comment": "Комментарий",
    "rate": "Ставка",
    "label": "Название",
    "reason": "Причина",
    "message_to_client": "Сообщение клиенту",
    "comment_internal": "Комментарий",
    "company_id": "Компания",
    "doc_type": "Тип документа",
    "file": "Файл",
    "offers": "Варианты",
    "info_items": "Запрос",
    "app_id": "Заявка",
    "doc_id": "Документ",
    "offer_id": "Предложение",
    "scope": "Раздел",
    "value": "Значение",
    "status": "Статус",
    "q": "Поиск",
    "product": "Продукт",
    "service": "Сервис",
    "mode": "Режим",
    "duration_seconds": "Длительность",
    "purpose_code": "Цель",
    "has_collateral": "Залог",
    "owner_guarantee": "Поручительство",
    "with_suggestions": "Подсказки",
    "parent_id": "Исходная заявка",
    "fields": "Поля",
    "type": "Тип",
    "action": "Действие",
}


def _num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:,.0f}".replace(",", " ") if f == int(f) else str(v)


def _describe(err: dict) -> str:
    t, ctx = err.get("type", ""), err.get("ctx") or {}
    messages = {
        "missing": "обязательное поле",
        "string_too_short": "слишком короткое значение",
        "uuid_parsing": "некорректный идентификатор",
        "literal_error": "недопустимое значение",
        "too_long": "слишком много элементов",
        "json_invalid": "некорректный JSON",
    }
    if t in messages:
        return messages[t]
    if t == "greater_than":
        return f"должно быть больше {_num(ctx.get('gt'))}"
    if t == "greater_than_equal":
        return f"должно быть не меньше {_num(ctx.get('ge'))}"
    if t == "less_than_equal":
        return f"должно быть не больше {_num(ctx.get('le'))}"
    if t == "less_than":
        return f"должно быть меньше {_num(ctx.get('lt'))}"
    if t == "string_too_long":
        return f"слишком длинное значение (не больше {ctx.get('max_length')} символов)"
    if t in {"float_parsing", "int_parsing", "float_type", "int_type", "finite_number", "int_from_float"}:
        return "должно быть числом"
    if t == "value_error":
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        if "UploadFile" in msg:
            return "приложите файл"
        # собственные проверки пишут по-русски; служебные английские сообщения клиенту не показываем
        return msg if re.search("[А-Яа-яЁё]", msg) else "некорректное значение"
    if t in {"bool_parsing", "bool_type"}:
        return "должно быть да или нет"
    if t in {"dict_type", "model_type", "model_attributes_type"}:
        return "неверный формат данных"
    if t in {"list_type", "tuple_type"}:
        return "должно быть списком"
    if t in {"string_type"}:
        return "должно быть текстом"
    return "некорректное значение"


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    problems = []
    for err in exc.errors():
        field = next(
            (str(part) for part in reversed(err.get("loc", ())) if isinstance(part, str) and part != "body"), ""
        )
        label = FIELD_LABELS.get(field, field)
        text = _describe(err)
        problems.append(f"{label}: {text}" if label else text)
    return JSONResponse(
        status_code=422, content={"detail": {"message": "Проверьте введённые данные", "problems": problems}}
    )


# Ответы фреймворка по умолчанию (английские) — по-русски: клиент видит их в интерфейсе
GENERIC_DETAILS = {
    400: "Не удалось прочитать запрос — проверьте формат данных",
    401: "Требуется вход в систему",
    403: "Недостаточно прав",
    404: "Не найдено",
    405: "Метод не поддерживается",
    413: "Слишком большой запрос",
    415: "Неподдерживаемый формат данных",
}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, str) and exc.detail.isascii():
        exc = StarletteHTTPException(
            exc.status_code, GENERIC_DETAILS.get(exc.status_code, "Ошибка обработки запроса"), headers=exc.headers
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(DataError)
async def data_error(request: Request, exc: DataError):
    # Например, символ NUL в параметре запроса: такой текст PostgreSQL не сохраняет. Параметры в лог не пишем
    log.warning("данные отклонены базой", extra={"path": request.url.path, "error": type(exc.orig).__name__})
    return JSONResponse(
        status_code=422,
        content={"detail": {"message": "Проверьте введённые данные", "problems": ["недопустимые символы в данных"]}},
    )


@app.exception_handler(StaleDataError)
async def stale_data_error(request: Request, exc: StaleDataError):
    # Оптимистичная блокировка: заявку параллельно изменил другой запрос или воркер
    return JSONResponse(status_code=409, content={"detail": "Заявка изменилась, обновите страницу"})


@app.exception_handler(TransitionError)
async def transition_error(request: Request, exc: TransitionError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.include_router(system.router)
app.include_router(auth.router)
app.include_router(client.router)
app.include_router(applications.router)
app.include_router(bank.router)
