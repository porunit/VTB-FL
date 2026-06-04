"""
API оператора (этап 1). Единственная точка ввода денег в систему.

  POST   /api/payments              — зафиксировать платёж (получив наличку)
  PATCH  /api/rental_months/{id}    — причина неплатежа / статус месяца (заморозка)
  GET    /api/rental_months/{id}    — текущее состояние месяца (баланс)

Деньги вносит только оператор (entered_by). Баланс не хранится — он всегда
пересчитывается из журнала payments (см. db_model / v_rental_month_balance).
Поле collector_id — кто физически принёс наличку (информационно, баланс не двигает).
"""

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from db import get_session
from models import (
    Driver, NonpaymentReason, Payment, PaymentSource, RentalMonth, RMonthStatus,
    User, UserRole,
)

router = APIRouter(prefix='/api', tags=['operator'])

DEFAULT_OPERATOR = 'operator'


def _operator_user(session):
    """Дефолтный оператор (entered_by). Позже заменится реальной аутентификацией."""
    u = session.query(User).filter_by(name=DEFAULT_OPERATOR).one_or_none()
    if u is None:
        u = User(name=DEFAULT_OPERATOR, role=UserRole.operator)
        session.add(u)
        session.flush()
    return u


def _paid_and_balance(session, rm):
    """(paid, balance) месяца по журналу. balance<0 — долг."""
    paid = session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.rental_month_id == rm.id,
               Payment.source != PaymentSource.buyout)
    ) or 0
    paid = float(paid)
    return paid, round(paid - float(rm.obligation), 2)


# --------- схемы ---------
class PaymentIn(BaseModel):
    amount: float = Field(..., description='Сумма платежа (≠ 0)')
    paid_at: date = Field(..., description='Дата получения денег')
    rental_month_id: int | None = Field(None, description='Либо id месяца напрямую…')
    driver_id: int | None = Field(None, description='…либо водитель + период')
    year: int | None = None
    month: int | None = Field(None, ge=1, le=12)
    source: PaymentSource = PaymentSource.cash
    collector_id: int | None = Field(None, description='кто принёс наличку (информац.)')
    note: str | None = None


class PaymentOut(BaseModel):
    id: int
    rental_month_id: int
    amount: float
    paid_at: date
    source: PaymentSource
    month_paid: float
    month_balance: float


class RentalMonthPatch(BaseModel):
    reason: NonpaymentReason | None = None
    status: RMonthStatus | None = None
    clear_reason: bool = False


class RentalMonthOut(BaseModel):
    id: int
    driver_id: int
    year: int
    month: int
    obligation: float
    paid: float
    balance: float
    status: RMonthStatus
    reason: NonpaymentReason | None


# --------- хелперы ---------
def _resolve_rm(session, body):
    if body.rental_month_id is not None:
        rm = session.get(RentalMonth, body.rental_month_id)
        if rm is None:
            raise HTTPException(404, f'rental_month {body.rental_month_id} не найден')
        return rm
    if body.driver_id and body.year and body.month:
        rms = session.execute(
            select(RentalMonth).where(
                RentalMonth.driver_id == body.driver_id,
                RentalMonth.year == body.year,
                RentalMonth.month == body.month)
        ).scalars().all()
        if not rms:
            raise HTTPException(404, 'Нет месяца для этого водителя/периода')
        if len(rms) > 1:
            raise HTTPException(409, 'У водителя несколько аренд в этом месяце — '
                                     'укажите rental_month_id явно')
        return rms[0]
    raise HTTPException(422, 'Укажите rental_month_id ИЛИ driver_id + year + month')


# --------- эндпоинты ---------
@router.post('/payments', response_model=PaymentOut, status_code=201)
def create_payment(body: PaymentIn):
    if body.amount == 0:
        raise HTTPException(422, 'amount не может быть 0')
    session = get_session()
    try:
        rm = _resolve_rm(session, body)
        op = _operator_user(session)
        pay = Payment(
            rental_month_id=rm.id, amount=round(body.amount, 2), paid_at=body.paid_at,
            source=body.source, entered_by=op.id, collector_id=body.collector_id,
            note=body.note)
        session.add(pay)
        session.flush()
        paid, balance = _paid_and_balance(session, rm)
        session.commit()
        return PaymentOut(id=pay.id, rental_month_id=rm.id, amount=float(pay.amount),
                          paid_at=pay.paid_at, source=pay.source,
                          month_paid=paid, month_balance=balance)
    finally:
        session.close()


@router.get('/rental_months/{rm_id}', response_model=RentalMonthOut)
def get_rental_month(rm_id: int):
    session = get_session()
    try:
        rm = session.get(RentalMonth, rm_id)
        if rm is None:
            raise HTTPException(404, 'rental_month не найден')
        paid, balance = _paid_and_balance(session, rm)
        return RentalMonthOut(id=rm.id, driver_id=rm.driver_id, year=rm.year, month=rm.month,
                              obligation=float(rm.obligation), paid=paid, balance=balance,
                              status=rm.status, reason=rm.reason)
    finally:
        session.close()


@router.patch('/rental_months/{rm_id}', response_model=RentalMonthOut)
def patch_rental_month(rm_id: int, body: RentalMonthPatch):
    session = get_session()
    try:
        rm = session.get(RentalMonth, rm_id)
        if rm is None:
            raise HTTPException(404, 'rental_month не найден')
        if body.clear_reason:
            rm.reason = None
        elif body.reason is not None:
            rm.reason = body.reason
        if body.status is not None:
            rm.status = body.status
        session.commit()
        paid, balance = _paid_and_balance(session, rm)
        return RentalMonthOut(id=rm.id, driver_id=rm.driver_id, year=rm.year, month=rm.month,
                              obligation=float(rm.obligation), paid=paid, balance=balance,
                              status=rm.status, reason=rm.reason)
    finally:
        session.close()
