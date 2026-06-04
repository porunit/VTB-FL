"""
ORM-модели системы учёта (этап 0). Соответствуют docs/db-schema.md.

Единица учёта — RentalMonth (аренда × месяц). Деньги — журнал Payment, который
пишет только оператор (entered_by). Полевой слой сборщика (CollectionTask) и
договоры (Document) денежный баланс не двигают.

Знак: obligation хранится положительным (99000 = «начислено за месяц»),
balance = Σ payments − obligation, отрицательный balance = долг (см. db-schema.md §2).
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey, Numeric,
    SmallInteger, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


# ----- Перечисления -----
class UserRole(str, enum.Enum):
    operator = 'operator'
    collector = 'collector'
    owner = 'owner'
    admin = 'admin'


class PaymentSource(str, enum.Enum):
    cash = 'cash'
    buyout = 'buyout'
    withholding = 'withholding'
    return_ = 'return'
    other = 'other'


class RMonthStatus(str, enum.Enum):
    open = 'open'
    closed = 'closed'
    debt = 'debt'
    frozen = 'frozen'


class NonpaymentReason(str, enum.Enum):
    accident = 'accident'
    illness = 'illness'
    forgot = 'forgot'
    stalling = 'stalling'
    malicious = 'malicious'
    other = 'other'


class FieldStatus(str, enum.Enum):
    pending = 'pending'
    visited = 'visited'
    promised = 'promised'
    refused = 'refused'
    absent = 'absent'


class DocumentType(str, enum.Enum):
    contract = 'contract'
    addendum = 'addendum'
    receipt = 'receipt'
    other = 'other'


_money = Numeric(12, 2)


def _created():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ----- Таблицы -----
class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name='user_role'), nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()


class Driver(Base):
    __tablename__ = 'drivers'
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(Text)
    in_park: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()

    rentals: Mapped[list['Rental']] = relationship(back_populates='driver')


class Car(Base):
    __tablename__ = 'cars'
    id: Mapped[int] = mapped_column(primary_key=True)
    plate: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    model: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()


class Rental(Base):
    __tablename__ = 'rentals'
    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey('drivers.id'), nullable=False)
    car_id: Mapped[int] = mapped_column(ForeignKey('cars.id'), nullable=False)
    daily_rate: Mapped[float] = mapped_column(_money, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()

    driver: Mapped['Driver'] = relationship(back_populates='rentals')
    car: Mapped['Car'] = relationship()
    __table_args__ = (UniqueConstraint('driver_id', 'car_id', name='uq_rental_driver_car'),)


class RentalMonth(Base):
    __tablename__ = 'rental_months'
    id: Mapped[int] = mapped_column(primary_key=True)
    rental_id: Mapped[int] = mapped_column(ForeignKey('rentals.id'), nullable=False)
    driver_id: Mapped[int] = mapped_column(ForeignKey('drivers.id'), nullable=False)
    car_id: Mapped[int] = mapped_column(ForeignKey('cars.id'), nullable=False)
    year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    obligation: Mapped[float] = mapped_column(_money, nullable=False)
    daily_rate: Mapped[float] = mapped_column(_money, nullable=False)
    status: Mapped[RMonthStatus] = mapped_column(
        Enum(RMonthStatus, name='rmonth_status'), default=RMonthStatus.open, nullable=False)
    reason: Mapped[NonpaymentReason | None] = mapped_column(
        Enum(NonpaymentReason, name='nonpayment_reason'))
    created_at: Mapped[datetime] = _created()

    payments: Mapped[list['Payment']] = relationship(back_populates='rental_month')
    __table_args__ = (
        UniqueConstraint('rental_id', 'year', 'month', name='uq_rmonth_rental_period'),
        CheckConstraint('month BETWEEN 1 AND 12', name='ck_rmonth_month'),
    )


class Payment(Base):
    __tablename__ = 'payments'
    id: Mapped[int] = mapped_column(primary_key=True)
    rental_month_id: Mapped[int] = mapped_column(ForeignKey('rental_months.id'), nullable=False)
    amount: Mapped[float] = mapped_column(_money, nullable=False)
    paid_at: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[PaymentSource] = mapped_column(
        Enum(PaymentSource, name='payment_source'), default=PaymentSource.cash, nullable=False)
    entered_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    collector_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    # Аннулирование (append-only журнал): платёж не удаляется и не правится,
    # а помечается аннулированным. В баланс/paid входят только voided_at IS NULL.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    void_reason: Mapped[str | None] = mapped_column(Text)

    rental_month: Mapped['RentalMonth'] = relationship(back_populates='payments')
    __table_args__ = (CheckConstraint('amount <> 0', name='ck_payment_amount_nonzero'),)


class Document(Base):
    __tablename__ = 'documents'
    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey('drivers.id'), nullable=False)
    rental_id: Mapped[int | None] = mapped_column(ForeignKey('rentals.id'))
    type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name='document_type'), default=DocumentType.contract, nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    is_collection_basis: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = _created()


class Note(Base):
    __tablename__ = 'notes'
    id: Mapped[int] = mapped_column(primary_key=True)
    rental_month_id: Mapped[int | None] = mapped_column(ForeignKey('rental_months.id'))
    driver_id: Mapped[int] = mapped_column(ForeignKey('drivers.id'), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = _created()


class CollectionTask(Base):
    __tablename__ = 'collection_tasks'
    id: Mapped[int] = mapped_column(primary_key=True)
    rental_month_id: Mapped[int] = mapped_column(ForeignKey('rental_months.id'), nullable=False)
    driver_id: Mapped[int] = mapped_column(ForeignKey('drivers.id'), nullable=False)
    assigned_collector: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    field_status: Mapped[FieldStatus] = mapped_column(
        Enum(FieldStatus, name='field_status'), default=FieldStatus.pending, nullable=False)
    promised_amount: Mapped[float | None] = mapped_column(_money)
    promised_date: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_at: Mapped[datetime] = _created()
