import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.domain.enums import DOC_TYPES, POSITION_LABELS, PURPOSES, AppStatus, Role
from app.domain.statuses import BANK_LABELS, CLIENT_STATUS
from app.models import Application, Company, CompanyMember, Loan, Notification, Product, User
from app.schemas import (
    CompanyProfileOut,
    DictionariesOut,
    LoanOut,
    MatchIn,
    MatchOut,
    NotificationsOut,
    ProductOut,
)
from app.security import get_current_user, require_company_member, require_roles
from app.services import assessment
from app.services.views import as_of_today, loan_out, product_out, revenue_estimate
from app.services.workflow import get_setting, key_rate

router = APIRouter(prefix="/api/v1", tags=["Клиент: компания, продукты, кредиты"])
settings = get_settings()
client_only = require_roles(Role.CLIENT)


@router.get("/dictionaries", response_model=DictionariesOut, summary="Справочники: цели, документы, статусы, ключевая ставка")
def dictionaries(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {
        "purposes": [{"code": k, **v} for k, v in PURPOSES.items()],
        "doc_types": {k: v for k, v in DOC_TYPES.items()},
        "statuses": {
            s: {"client": CLIENT_STATUS[AppStatus(s)][0], "bank": BANK_LABELS[AppStatus(s)]} for s in AppStatus
        },
        "key_rate": key_rate(db),
        "key_rate_note": get_setting(db, "key_rate_note"),
        "demo_mode": settings.demo_mode,
    }


@router.get("/products", response_model=list[ProductOut], summary="Каталог кредитных продуктов")
def products(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [product_out(p) for p in db.scalars(select(Product).where(Product.is_active.is_(True)).order_by(Product.sort_order))]


@router.get("/companies/{company_id}", response_model=CompanyProfileOut, summary="Профиль компании: данные, обороты, предодобренный лимит")
def company_profile(company_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    require_company_member(db, user, company_id)
    company = db.get(Company, company_id)
    stats = assessment.company_turnover(db, company.id)
    members = db.scalars(select(CompanyMember).where(CompanyMember.company_id == company.id))
    return {
        **{c: getattr(company, c) for c in (
            "id", "inn", "ogrn", "kind", "short_name", "full_name", "okved_code", "okved_name",
            "registration_date", "legal_address", "client_since", "segment", "settlement_account", "headcount",
        )},
        "members": [
            {"full_name": m.user.full_name, "position_label": POSITION_LABELS.get(m.position, m.position), "can_sign": m.can_sign}
            for m in members
        ],
        "turnover": {
            "months": stats.months,
            "avg_monthly": round(stats.avg_monthly, 2),
            "trend": round(stats.trend, 4),
            "cv": round(stats.cv, 4),
            "series": stats.series,
        },
        "preapproved": assessment.preapproved_limit(db, company, key_rate(db), as_of_today()),
        "revenue_estimate": revenue_estimate(stats.avg_monthly),
    }


@router.post("/products/match", response_model=MatchOut, summary="Подбор продукта под задачу бизнеса")
def match(body: MatchIn, db: Session = Depends(get_db), user: User = Depends(client_only)):
    require_company_member(db, user, body.company_id)
    if body.purpose not in PURPOSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестная цель кредита")
    company = db.get(Company, body.company_id)
    kr = key_rate(db)
    return {
        "key_rate": kr,
        "results": assessment.match_products(db, company, body.purpose, body.amount, body.term_months, kr, as_of_today()),
    }


@router.get("/loans", response_model=list[LoanOut], summary="Выданные кредиты компании")
def loans(company_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    require_company_member(db, user, company_id)
    rows = db.scalars(select(Loan).where(Loan.company_id == company_id).order_by(Loan.issued_at.desc()))
    return [loan_out(loan, db.get(Application, loan.application_id)) for loan in rows]


@router.get("/loans/{loan_id}", response_model=LoanOut, summary="Кредит с графиком платежей")
def loan_detail(loan_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(client_only)):
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Кредит не найден")
    require_company_member(db, user, loan.company_id)
    return loan_out(loan, db.get(Application, loan.application_id), with_schedule=True)


@router.get("/notifications", response_model=NotificationsOut, summary="Уведомления в кабинете")
def notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    items = list(
        db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(30))
    )
    unread = sum(1 for n in items if not n.is_read)
    return {"unread": unread, "items": items}


@router.post("/notifications/read-all", status_code=204, summary="Отметить уведомления прочитанными")
def read_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.execute(update(Notification).where(Notification.user_id == user.id).values(is_read=True))
    db.commit()
