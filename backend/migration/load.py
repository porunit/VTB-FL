"""
Загрузка выгруженных записей (водитель × месяц) в БД — идемпотентно.

Натуральные ключи upsert: driver.normalized_name, car.plate, rental(driver,car),
rental_month(rental, year, month). Платежи миграции помечены системным
пользователем «migration» (entered_by); при повторном прогоне они удаляются и
переписываются, а введённые оператором платежи (другой entered_by) не трогаются.

Резолв машины: номер есть не во всех месяцах — переносим последний известный
вперёд и назад по времени; если у водителя номера нет вовсе — ставим плейсхолдер
«Б/Н {имя}» (rental требует машину). Мусорные значения (напр. «0.0») игнорируем.
"""

import re
from collections import defaultdict
from datetime import date

from models import (
    Car, Driver, Payment, PaymentSource, Rental, RentalMonth, User, UserRole,
)

MIGRATION_USER = 'migration'


def _is_real_plate(s):
    return bool(s) and bool(re.search(r'[A-Za-zА-Яа-я]', s))


def resolve_cars(records):
    """index записи → номер машины (с переносом и плейсхолдером)."""
    by_driver = defaultdict(list)
    for i, r in enumerate(records):
        by_driver[r['key']].append(i)

    plate_for = {}
    for key, idxs in by_driver.items():
        idxs.sort(key=lambda i: (records[i]['year'], records[i]['month']))
        last = None
        for i in idxs:
            p = (records[i]['car'] or '').strip()
            p = p if _is_real_plate(p) else ''
            if p:
                last = p
            plate_for[i] = p or last
        # обратная заливка ведущих пропусков
        nxt = None
        for i in reversed(idxs):
            if plate_for[i]:
                nxt = plate_for[i]
            elif nxt:
                plate_for[i] = nxt
        # плейсхолдер, если номера нет вовсе
        if not any(plate_for[i] for i in idxs):
            ph = f"Б/Н {records[idxs[0]]['name']}"[:32]
            for i in idxs:
                plate_for[i] = ph
    return plate_for


def _migration_user(session):
    u = session.query(User).filter_by(name=MIGRATION_USER).one_or_none()
    if u is None:
        u = User(name=MIGRATION_USER, role=UserRole.admin, is_active=False)
        session.add(u)
        session.flush()
    return u


def load(records, session, corrections=None):
    """Идемпотентно грузит записи. Возвращает счётчики созданного.

    corrections — список исправлений (см. migration/corrections.py): деньги,
    введённые в листе текстом и не учтённые формулой. Грузятся как настоящие
    платежи с пометкой, отдельно считаются в counts['corrections'].
    """
    mig = _migration_user(session)
    plate_for = resolve_cars(records)
    counts = defaultdict(int)

    driver_cache, car_cache, rental_cache = {}, {}, {}
    rm_index = {}  # (driver_key, year, month) -> RentalMonth (для исправлений)

    def get_driver(rec):
        nk = rec['key']
        d = driver_cache.get(nk)
        if d is None:
            d = session.query(Driver).filter_by(normalized_name=nk).one_or_none()
            if d is None:
                d = Driver(full_name=rec['name'], normalized_name=nk)
                session.add(d); session.flush(); counts['drivers'] += 1
            driver_cache[nk] = d
        return d

    def get_car(plate):
        c = car_cache.get(plate)
        if c is None:
            c = session.query(Car).filter_by(plate=plate).one_or_none()
            if c is None:
                c = Car(plate=plate)
                session.add(c); session.flush(); counts['cars'] += 1
            car_cache[plate] = c
        return c

    def get_rental(driver, car, daily_rate):
        rk = (driver.id, car.id)
        r = rental_cache.get(rk)
        if r is None:
            r = session.query(Rental).filter_by(driver_id=driver.id, car_id=car.id).one_or_none()
            if r is None:
                r = Rental(driver_id=driver.id, car_id=car.id, daily_rate=daily_rate or 0)
                session.add(r); session.flush(); counts['rentals'] += 1
            rental_cache[rk] = r
        if daily_rate:
            r.daily_rate = daily_rate
        return r

    for i, rec in enumerate(records):
        driver = get_driver(rec)
        car = get_car(plate_for[i])
        rental = get_rental(driver, car, rec['daily_rate'])

        obligation_db = round(-rec['sheet_obligation'], 2)  # лист хранит минусом → плюс
        rm = (session.query(RentalMonth)
              .filter_by(rental_id=rental.id, year=rec['year'], month=rec['month'])
              .one_or_none())
        if rm is None:
            rm = RentalMonth(rental_id=rental.id, driver_id=driver.id, car_id=car.id,
                             year=rec['year'], month=rec['month'],
                             obligation=obligation_db, daily_rate=rec['daily_rate'] or 0)
            session.add(rm); session.flush(); counts['rental_months'] += 1
        else:
            rm.obligation = obligation_db
            rm.daily_rate = rec['daily_rate'] or 0
            rm.driver_id, rm.car_id = driver.id, car.id

        rm_index[(rec['key'], rec['year'], rec['month'])] = rm

        # Платежи миграции: снести старые, переписать заново (идемпотентность).
        session.query(Payment).filter_by(rental_month_id=rm.id, entered_by=mig.id).delete()
        for pay_date, amount in rec['payments']:
            session.add(Payment(rental_month_id=rm.id, amount=amount, paid_at=pay_date,
                                source=PaymentSource.cash, entered_by=mig.id))
            counts['payments'] += 1

    # Исправления: учитываем «оплошности оператора» как настоящие платежи.
    for c in (corrections or []):
        rm = rm_index.get((c['key'], c['year'], c['month']))
        if rm is None:
            continue  # нет соответствующего месяца — пропускаем
        session.add(Payment(
            rental_month_id=rm.id, amount=round(c['amount'], 2),
            paid_at=date.fromisoformat(c['date']),
            source=PaymentSource.cash, entered_by=mig.id, note=c.get('note')))
        counts['payments'] += 1
        counts['corrections'] += 1

    session.commit()
    return counts
