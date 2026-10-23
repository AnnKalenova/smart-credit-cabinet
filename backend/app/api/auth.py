from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.domain.enums import POSITION_LABELS
from app.models import CompanyMember, LoginAttempt, User
from app.schemas import DemoAccount, LoginIn, MeOut, TokenOut
from app.security import create_access_token, get_current_user, hash_password, verify_password
from app.seed import DEMO_ACCOUNTS
from app.services.workflow import utcnow

router = APIRouter(prefix="/api/v1/auth", tags=["Аутентификация"])
settings = get_settings()

# Защита от подбора пароля (окно — 10 минут, успешный вход обнуляет счётчики логина):
#  - 10 неудачных попыток по логину с одного адреса — вход по нему с этого адреса закрыт. Чужой адрес
#    не может заблокировать пользователя, который входит со своего;
#  - 50 неудачных попыток по логину со всех адресов — защита от подбора с множества адресов;
#  - 100 неудачных попыток с одного адреса по любым логинам — защита от перебора логинов.
LOGIN_FAILURES_LIMIT = 10
LOGIN_GLOBAL_FAILURES_LIMIT = 50
IP_FAILURES_LIMIT = 100
LOGIN_LOCK_WINDOW = timedelta(minutes=10)
# Для несуществующего логина пароль всё равно проверяется — время ответа не выдаёт, есть ли такой пользователь
_DUMMY_HASH = hash_password("not-a-real-password")


def _failures(db: Session, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.success.is_(False), *conditions))


def _lock_reason(db: Session, login: str, ip: str | None) -> str | None:
    window_start = utcnow() - LOGIN_LOCK_WINDOW
    if ip and _failures(db, LoginAttempt.ip == ip, LoginAttempt.created_at > window_start) >= IP_FAILURES_LIMIT:
        return "Слишком много неудачных попыток входа с вашего адреса. Попробуйте через 10 минут"
    since = window_start
    last_success = db.scalar(
        select(func.max(LoginAttempt.created_at)).where(LoginAttempt.login == login, LoginAttempt.success.is_(True))
    )
    if last_success and last_success > since:
        since = last_success
    by_login = (LoginAttempt.login == login, LoginAttempt.created_at > since)
    same_ip = LoginAttempt.ip.is_(None) if ip is None else LoginAttempt.ip == ip
    if (
        _failures(db, *by_login, same_ip) >= LOGIN_FAILURES_LIMIT
        or _failures(db, *by_login) >= LOGIN_GLOBAL_FAILURES_LIMIT
    ):
        return "Слишком много неудачных попыток входа. Попробуйте через 10 минут"
    return None


@router.post(
    "/login",
    response_model=TokenOut,
    summary="Вход по логину и паролю",
    responses={401: {"description": "Неверный логин или пароль"}, 429: {"description": "Слишком много попыток"}},
)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    name = body.login.strip().lower()
    key = name[:64]  # строчные буквы в Unicode бывают длиннее исходных — ключ по размеру колонки
    # Попытки по одному логину проверяются по очереди: параллельные запросы не обходят лимит.
    # Блокировка транзакционная и снимается при commit.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"login:{key}"})
    ip = request.client.host[:64] if request.client and request.client.host else None
    reason = _lock_reason(db, key, ip)
    if reason:
        db.rollback()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, reason)
    user = db.scalar(select(User).where(func.lower(User.login) == name))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH) and bool(user and user.is_active)
    db.add(LoginAttempt(login=key, success=ok, ip=ip))
    db.commit()
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    return {"access_token": create_access_token(user), "user": user}


@router.get("/me", response_model=MeOut, summary="Текущий пользователь и его компании")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    companies = []
    for m in db.scalars(select(CompanyMember).where(CompanyMember.user_id == user.id)):
        companies.append(
            {
                "id": m.company.id,
                "inn": m.company.inn,
                "short_name": m.company.short_name,
                "kind": m.company.kind,
                "position": m.position,
                "position_label": POSITION_LABELS.get(m.position, m.position),
                "can_sign": m.can_sign,
            }
        )
    return {"user": user, "companies": companies}


@router.get("/demo-accounts", response_model=list[DemoAccount], summary="Демо-учётки (только в демо-режиме)")
def demo_accounts():
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Недоступно")
    return DEMO_ACCOUNTS
