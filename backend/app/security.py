"""Аутентификация (JWT) и ролевая модель доступа.

На стенде вход по логину и паролю. В промышленной эксплуатации кабинет встраивается в интернет-банк
для бизнеса и получает пользователя через SSO (OAuth 2.0 / OpenID Connect), поэтому отдельная
регистрация не нужна: предприниматель уже клиент банка.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.domain.enums import Role
from app.models import Application, CompanyMember, User

settings = get_settings()
bearer = HTTPBearer(auto_error=False)
PBKDF2_ITERATIONS = 210_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), hash_hex)


def hash_code(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


def create_access_token(user: User) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
        "iss": "smart-credit-cabinet",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход в систему")
    try:
        payload = jwt.decode(
            creds.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer="smart-credit-cabinet",
            options={"require": ["exp", "sub", "iss"]},  # токен без срока действия не принимаем
        )
        user_id = uuid.UUID(str(payload["sub"]))
    except (jwt.PyJWTError, ValueError, TypeError) as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла, войдите снова") from e
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    return user


def require_roles(*roles: Role):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user

    return dependency


def membership(db: Session, user: User, company_id: uuid.UUID) -> CompanyMember | None:
    return db.scalar(
        select(CompanyMember).where(CompanyMember.user_id == user.id, CompanyMember.company_id == company_id)
    )


def require_company_member(db: Session, user: User, company_id: uuid.UUID) -> CompanyMember:
    m = membership(db, user, company_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Компания не найдена")
    return m


def get_client_application(db: Session, user: User, app_id: uuid.UUID) -> tuple[Application, CompanyMember]:
    """Клиент видит только заявки своих компаний (проверка на уровне каждой записи)."""
    app = db.get(Application, app_id)
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    m = membership(db, user, app.company_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return app, m
