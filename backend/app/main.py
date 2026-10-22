"""API кредитного конвейера «Умный кредитный кабинет предпринимателя»."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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
    response = await call_next(request)
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
