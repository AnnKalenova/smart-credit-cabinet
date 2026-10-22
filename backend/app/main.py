"""API кредитного конвейера «Умный кредитный кабинет предпринимателя»."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from sqlalchemy.orm.exc import StaleDataError

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
Прототип цифрового кредитного конвейера для малого и микробизнеса.

**Клиентский путь:** подбор продукта → черновик заявки с предзаполненными данными → согласия
(простая ЭП кодом внутри продукта) → отправка → уточнения → предложение → подписание → выдача.

**Банковская обработка:** проверка полноты → сбор данных (ЕГРЮЛ/ЕГРИП, ФНС, БКИ — симуляторы) →
скоринг (балльная модель) → кредитная политика → автоматическое решение или ручное рассмотрение.

Авторизация: `POST /api/v1/auth/login` → кнопка **Authorize** → `Bearer <access_token>`.
Демо-учётки: `GET /api/v1/auth/demo-accounts`, пароль у всех `demo1234`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    if settings.jwt_secret in DEFAULT_SECRETS:
        if not settings.demo_mode:
            # вне демо-режима сервис не стартует с общеизвестным секретом подписи токенов
            raise RuntimeError("Задайте собственный JWT_SECRET: значение по умолчанию допустимо только в демо-режиме")
        log.warning("используется демо-значение JWT_SECRET — смените его вне демо-стенда")
    log.info("API запущен", extra={"environment": settings.environment})
    yield


app = FastAPI(
    title="Умный кредитный кабинет предпринимателя — API",
    version="1.0.0",
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


@app.middleware("http")
async def observe(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
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
        return str(err.get("msg", "")).removeprefix("Value error, ")
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
