"""
API оператора (этап 1). Единственная точка ввода денег в систему.

  POST   /api/payments              — зафиксировать платёж (получив наличку)
  PATCH  /api/rental_months/{id}    — причина неплатежа / статус месяца (заморозка)
  GET    /api/rental_months/{id}    — текущее состояние месяца (баланс)

Деньги вносит только оператор (entered_by). Баланс не хранится — он всегда
пересчитывается из журнала payments (см. db_model / v_rental_month_balance).
Поле collector_id — кто физически принёс наличку (информационно, баланс не двигает).
"""

import calendar
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

MONTHS_RU = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
             'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']

from db import get_session
from models import (
    Car, Driver, NonpaymentReason, Payment, PaymentSource, RentalMonth,
    RMonthStatus, User, UserRole,
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
               Payment.source != PaymentSource.buyout,
               Payment.voided_at.is_(None))
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


class VoidIn(BaseModel):
    reason: str | None = None


@router.post('/payments/{pid}/void', response_model=PaymentOut)
def void_payment(pid: int, body: VoidIn):
    """Аннулировать платёж (append-only: не удаляем, помечаем). Идемпотентно — 409
    если уже аннулирован. Для исправления: аннулируй ошибочный, добавь верный."""
    session = get_session()
    try:
        p = session.get(Payment, pid)
        if p is None:
            raise HTTPException(404, f'payment {pid} не найден')
        if p.voided_at is not None:
            raise HTTPException(409, 'платёж уже аннулирован')
        op = _operator_user(session)
        p.voided_at = datetime.now(timezone.utc)
        p.voided_by = op.id
        p.void_reason = body.reason
        session.flush()
        rm = session.get(RentalMonth, p.rental_month_id)
        paid, balance = _paid_and_balance(session, rm)
        session.commit()
        return PaymentOut(id=p.id, rental_month_id=p.rental_month_id, amount=float(p.amount),
                          paid_at=p.paid_at, source=p.source,
                          month_paid=paid, month_balance=balance)
    finally:
        session.close()


@router.get('/month_grid')
def month_grid(year: int, month: int):
    """Сетка месяца как в исходной таблице: столбцы-водители, строки-дни,
    в ячейках — платежи за день. Шапка (ставка/обязательство/авто) и
    подвал (Итого/Осталось) — для ввода кликом по ячейке."""
    if not 1 <= month <= 12:
        raise HTTPException(422, 'month вне 1..12')
    session = get_session()
    try:
        rows = session.execute(
            select(RentalMonth, Driver.full_name, Car.plate)
            .join(Driver, Driver.id == RentalMonth.driver_id)
            .join(Car, Car.id == RentalMonth.car_id)
            .where(RentalMonth.year == year, RentalMonth.month == month)
            .order_by(Driver.full_name)
        ).all()

        rm_ids = [rm.id for rm, _, _ in rows]
        by_rm = defaultdict(lambda: defaultdict(list))
        if rm_ids:
            pays = session.execute(
                select(Payment).where(Payment.rental_month_id.in_(rm_ids))
                .order_by(Payment.created_at)
            ).scalars().all()
            for p in pays:
                by_rm[p.rental_month_id][p.paid_at.day].append({
                    'id': p.id, 'amount': float(p.amount),
                    'voided': p.voided_at is not None, 'source': p.source.value,
                })

        drivers = []
        for rm, name, plate in rows:
            days = by_rm.get(rm.id, {})
            paid = round(sum(pp['amount'] for d in days.values()
                             for pp in d if not pp['voided']
                             and pp['source'] != PaymentSource.buyout.value), 2)
            drivers.append({
                'rental_month_id': rm.id, 'driver_id': rm.driver_id, 'name': name,
                'car': plate, 'daily_rate': float(rm.daily_rate),
                'obligation': float(rm.obligation),
                'paid': paid, 'balance': round(paid - float(rm.obligation), 2),
                'status': rm.status.value, 'reason': rm.reason.value if rm.reason else None,
                'days': {str(d): v for d, v in days.items()},
            })

        return {
            'year': year, 'month': month, 'label': MONTHS_RU[month - 1],
            'daysInMonth': calendar.monthrange(year, month)[1],
            'drivers': drivers,
        }
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
