"""
Построение модели дашборда из БД — тот же формат, что parser.build_model отдаёт
из Google-таблицы (чтобы фронт /api/model не менять).

Приём: из БД восстанавливаем только «сырые» входы — records (водитель×месяц) и
months — и отдаём их в ту же parser.aggregate(). Вся агрегация (aging, статусы,
собираемость, pro-rata) остаётся общей с листовым путём → модели сопоставимы.

Знак: в БД obligation положительное; parser.aggregate ждёт «листовой» знак
(obligation отрицательное, remaining = obligation + paid, отрицательное = долг),
поэтому здесь конвертируем обратно: obligation_rec = −db.obligation.
"""

import calendar
from datetime import datetime, timezone as dt_timezone

from sqlalchemy import func, select

import parser as model_parser
from models import Car, Driver, Payment, PaymentSource, RentalMonth

MONTHS_RU = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
             'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']


def build_model_from_db(session, tz='Europe/Moscow'):
    now = datetime.now(model_parser._tzinfo(tz))
    today = {'y': now.year, 'm': now.month - 1, 'd': now.day}
    today_ms = int(datetime(today['y'], today['m'] + 1, today['d'],
                            tzinfo=dt_timezone.utc).timestamp() * 1000)

    # --- Месяцы: различные (year, month), хронологически ---
    periods = session.execute(
        select(RentalMonth.year, RentalMonth.month)
        .distinct().order_by(RentalMonth.year, RentalMonth.month)
    ).all()

    months = []
    index_by_period = {}
    for i, (yy, mm) in enumerate(periods):
        index_by_period[(yy, mm)] = i
        months.append({
            'index': i,
            'label': MONTHS_RU[mm - 1],
            'year': yy, 'month': mm - 1,          # parser хранит month 0-based
            'isCurrent': False,
            'daysInMonth': calendar.monthrange(yy, mm)[1],
            'daysElapsed': calendar.monthrange(yy, mm)[1],
            'sortKey': yy * 12 + (mm - 1),
        })

    # «Текущий» = самый поздний период (как в parser).
    if months:
        latest = max(months, key=lambda m: m['sortKey'])
        for m in months:
            m['isCurrent'] = (m['index'] == latest['index'])
            if m['isCurrent']:
                within = (m['year'] == today['y'] and m['month'] == today['m'])
                m['daysElapsed'] = min(today['d'], m['daysInMonth']) if within else m['daysInMonth']

    # --- Платежи: агрегаты на rental_month (касса = не выкуп) ---
    pay_rows = session.execute(
        select(
            Payment.rental_month_id,
            func.sum(Payment.amount),
            func.max(Payment.paid_at),
            func.count(Payment.id),
        )
        .where(Payment.source != PaymentSource.buyout,
               Payment.voided_at.is_(None))
        .group_by(Payment.rental_month_id)
    ).all()
    pay_map = {rm_id: (float(s or 0), mx, int(cnt)) for rm_id, s, mx, cnt in pay_rows}

    # Выкупы по месяцам (отдельный поток).
    buy_rows = session.execute(
        select(RentalMonth.year, RentalMonth.month, func.sum(Payment.amount))
        .join(Payment, Payment.rental_month_id == RentalMonth.id)
        .where(Payment.source == PaymentSource.buyout,
               Payment.voided_at.is_(None))
        .group_by(RentalMonth.year, RentalMonth.month)
    ).all()
    buyout_by_month = [
        {'monthIndex': index_by_period[(yy, mm)], 'total': float(t or 0)}
        for yy, mm, t in buy_rows if (yy, mm) in index_by_period and t
    ]

    # --- Записи водитель×месяц ---
    rows = session.execute(
        select(RentalMonth, Driver.full_name, Driver.normalized_name, Car.plate)
        .join(Driver, Driver.id == RentalMonth.driver_id)
        .join(Car, Car.id == RentalMonth.car_id)
    ).all()

    records = []
    for rm, full_name, norm_name, plate in rows:
        paid, last_dt, pay_days = pay_map.get(rm.id, (0.0, None, 0))
        obligation_rec = -float(rm.obligation)          # назад в «листовой» знак
        remaining = obligation_rec + paid
        records.append({
            'key': norm_name,
            'name': full_name,
            'car': plate,
            'monthIndex': index_by_period[(rm.year, rm.month)],
            'obligation': obligation_rec,
            'paid': paid,
            'remaining': remaining,
            'active': obligation_rec != 0,
            'lastPaymentDay': last_dt.day if last_dt else 0,
            'paymentDays': pay_days,
        })

    return model_parser.aggregate(records, months, buyout_by_month, today, today_ms, tz)
