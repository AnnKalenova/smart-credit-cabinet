from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.domain.enums import POSITION_LABELS
from app.models import CompanyMember, User
from app.schemas import DemoAccount, LoginIn, MeOut, TokenOut
from app.security import create_access_token, get_current_user, verify_password
from app.seed import DEMO_ACCOUNTS

router = APIRouter(prefix="/api/v1/auth", tags=["Аутентификация"])
settings = get_settings()


@router.post("/login", response_model=TokenOut, summary="Вход по логину и паролю")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.login == body.login.strip()))
    if user is None or not verify_password(body.password, user.password_hash) or not user.is_active:
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
