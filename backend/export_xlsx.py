"""
Экспорт БД обратно в .xlsx в формате ИСХОДНОЙ таблицы (вертикальные месячные блоки).

Назначение — страховка от потери данных и привычный вид: снимок можно открыть
в Excel/Google Sheets, проверить глазами, заархивировать, при катастрофе —
восстановиться. Запускается ежедневно (см. scripts/backup.sh).

Раскладка блока (как в оригинале):
  «Сумма/сутки:» | ставки по столбцам-водителям
  <Месяц>        | ФИО по столбцам
  «сумма мес.»   | обязательства (ОТРИЦАТЕЛЬНЫЕ, как в листе)
  «Номер авто»   | гос-номера
  даты 1..N      | платежи за день (положительные)
  «Итого»        | сумма платежей
  «Осталось»     | остаток (обязательство + оплата; <0 = долг)

Знак: в БД obligation положительное; здесь пишем минусом, чтобы выглядело и
парсилось как исходник. Аннулированные платежи и выкупы в снимок не входят.
"""

import calendar
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select

from db import get_session
from models import Car, Driver, Payment, PaymentSource, RentalMonth

MONTHS_RU = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
             'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']


def _gather(session):
    """{(year,month): [ {name, car, daily_rate, obligation, days{d: paid}} ]}, отсортировано."""
    rows = session.execute(
        select(RentalMonth, Driver.full_name, Car.plate)
        .join(Driver, Driver.id == RentalMonth.driver_id)
        .join(Car, Car.id == RentalMonth.car_id)
        .order_by(RentalMonth.year, RentalMonth.month, Driver.full_name)
    ).all()
    rm_ids = [rm.id for rm, _, _ in rows]

    pays_by_rm = {}
    if rm_ids:
        prs = session.execute(
            select(Payment).where(
                Payment.rental_month_id.in_(rm_ids),
                Payment.voided_at.is_(None),
                Payment.source != PaymentSource.buyout)
        ).scalars().all()
        for p in prs:
            pays_by_rm.setdefault(p.rental_month_id, {})
            d = p.paid_at.day
            pays_by_rm[p.rental_month_id][d] = pays_by_rm[p.rental_month_id].get(d, 0.0) + float(p.amount)

    blocks = {}
    for rm, name, plate in rows:
        blocks.setdefault((rm.year, rm.month), []).append({
            'name': name, 'car': plate or '',
            'daily_rate': float(rm.daily_rate), 'obligation': float(rm.obligation),
            'days': pays_by_rm.get(rm.id, {}),
        })
    return dict(sorted(blocks.items()))


def export_to_xlsx(session, path):
    wb = Workbook()
    ws = wb.active
    ws.title = 'ПЛАТЕЖИ'
    bold = Font(bold=True)
    r = 1

    def put(row, col, val, *, b=False, numfmt=None):
        c = ws.cell(row, col, val)
        if b:
            c.font = bold
        if numfmt:
            c.number_format = numfmt
        return c

    blocks = _gather(session)
    for (year, month), drivers in blocks.items():
        dim = calendar.monthrange(year, month)[1]
        ncol = len(drivers)

        put(r, 1, 'Сумма/сутки:', b=True)
        for j, d in enumerate(drivers):
            put(r, 2 + j, d['daily_rate'] or None)
        r += 1

        put(r, 1, f'{MONTHS_RU[month - 1]} {year}', b=True)
        for j, d in enumerate(drivers):
            put(r, 2 + j, d['name'], b=True)
        r += 1

        put(r, 1, 'сумма мес.', b=True)
        for j, d in enumerate(drivers):
            put(r, 2 + j, -d['obligation'] if d['obligation'] else 0)
        r += 1

        put(r, 1, 'Номер авто', b=True)
        for j, d in enumerate(drivers):
            put(r, 2 + j, d['car'])
        r += 1

        for day in range(1, dim + 1):
            put(r, 1, date(year, month, day), numfmt='DD.MM.YYYY')
            for j, d in enumerate(drivers):
                v = d['days'].get(day)
                if v:
                    put(r, 2 + j, round(v, 2))
            r += 1

        put(r, 1, 'Итого', b=True)
        for j, d in enumerate(drivers):
            put(r, 2 + j, round(sum(d['days'].values()), 2), b=True)
        r += 1

        put(r, 1, 'Осталось', b=True)
        for j, d in enumerate(drivers):
            paid = sum(d['days'].values())
            put(r, 2 + j, round(paid - d['obligation'], 2), b=True)
        r += 1
        r += 1  # пустая строка между блоками

    ws.freeze_panes = 'B1'
    wb.save(path)
    return {'months': len(blocks), 'path': path}


if __name__ == '__main__':
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else 'export.xlsx'
    session = get_session()
    try:
        info = export_to_xlsx(session, out)
    finally:
        session.close()
    print(f"Экспортировано: {info['months']} месяцев → {info['path']}")
