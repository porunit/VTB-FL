"""
Гранулярная выгрузка месячных блоков таблицы → плоские записи (водитель × месяц)
с ОТДЕЛЬНЫМИ дневными платежами (для журнала payments).

Отличие от parser.build_model: тот суммирует дневные платежи в paid и агрегирует
модель для дашборда. Здесь мы сохраняем каждую оплату по дате — это нужно для
переноса в журнал. Логика определения блоков/колонок/якорей повторяет parser
один-в-один (переиспользуем его помощники), а корректность гарантирует сверка
reconcile.py против собственных строк «Итого»/«Осталось» листа.

Знак: в листе обязательство отрицательное; здесь возвращаем как есть (sheet_obligation),
маппинг в положительное (obligation_db = −sheet) делает загрузчик.
"""

import calendar
import re
from datetime import date, datetime

from parser import (
    norm, clean_name, name_key, parse_num, row_type,
    is_month_name, is_vykup, is_sutki, month_from_label,
    find_total_col, formula_col_range, _cell,
)


def _block_starts(rows):
    return [i for i, r in enumerate(rows) if row_type(r) == 'sutki']


def extract_driver_months(rows, formulas, tz='Europe/Moscow'):
    """Список записей-словарей, по одной на (водитель × месяц).

    Каждая запись:
      key, name, car, daily_rate,
      year, month (1..12),
      sheet_obligation (отрицательное, как в листе),
      payments: [(date, amount), ...]   — только ЧИСЛОВЫЕ положительные дневные платежи,
      sheet_itogo, sheet_ostalos        — значения строк листа для сверки.

    Возвращает (records, anomalies). anomalies — дневные ячейки с ТЕКСТОМ,
    похожим на число (напр. '52 250'): формула SUM листа их не считает, поэтому
    и мы их не засчитываем в paid, а выносим оператору на исправление в источнике.
    """
    out = []
    anomalies = []
    starts = _block_starts(rows)

    for b, start in enumerate(starts):
        end = starts[b + 1] if b + 1 < len(starts) else len(rows)
        sutki_row = rows[start]
        names_row = rows[start + 1] if start + 1 < len(rows) else []

        driver_cols = {}
        month_label = ''
        for c, raw in enumerate(names_row):
            if raw is None or str(raw).strip() == '':
                continue
            nm = norm(raw)
            if is_month_name(nm):
                if not month_label:
                    month_label = clean_name(raw)
                continue
            if is_vykup(nm) or is_sutki(nm):
                continue
            driver_cols[c] = {'name': clean_name(raw), 'key': name_key(raw)}

        # Якорные строки блока.
        summa_mes_row = avto_row = itogo_row = ostalos_row = None
        avto_idx = itogo_idx = ostalos_idx = -1
        for r in range(start + 1, end):
            t = row_type(rows[r])
            if t == 'summaMes' and summa_mes_row is None:
                summa_mes_row = rows[r]
            elif t == 'avto' and avto_row is None:
                avto_row, avto_idx = rows[r], r
            elif t == 'itogo' and itogo_row is None:
                itogo_row, itogo_idx = rows[r], r
            elif t == 'ostalos' and ostalos_row is None:
                ostalos_row, ostalos_idx = rows[r], r

        # Ежедневные строки = data между «Номер авто» и «Итого».
        day_start = avto_idx + 1 if avto_idx >= 0 else start + 2
        day_end = itogo_idx if itogo_idx >= 0 else end
        data_rows = [rows[d] for d in range(day_start, day_end)
                     if 0 <= d < len(rows) and row_type(rows[d]) == 'data']

        # Граница колонок водителей по формуле «Осталось» (как в parser).
        total_col = find_total_col(summa_mes_row)
        col_range = None
        if ostalos_row is not None and total_col is not None and ostalos_idx >= 0:
            col_range = formula_col_range(
                _cell(formulas[ostalos_idx], total_col) if ostalos_idx < len(formulas) else None)
        if col_range:
            lo, hi = col_range
            for dck in list(driver_cols):
                if dck < lo or dck > hi or dck >= total_col:
                    del driver_cols[dck]
        elif total_col is not None:
            for dck in list(driver_cols):
                if dck >= total_col:
                    del driver_cols[dck]

        # Год/месяц блока — из ячеек-дат (надёжнее ярлыка).
        by, bm = -1, -1   # bm здесь 1..12
        for dr in data_rows:
            cell0 = _cell(dr, 0)
            if isinstance(cell0, (date, datetime)):
                by, bm = cell0.year, cell0.month
                break
        if bm < 0:
            bm0 = month_from_label(month_label)
            ym = re.search(r'(20\d{2})', str(month_label))
            by = int(ym.group(1)) if ym else datetime.now().year
            bm = (bm0 + 1) if bm0 >= 0 else datetime.now().month

        days_in_month = calendar.monthrange(by, bm)[1]

        for col in sorted(driver_cols):
            sheet_obl = parse_num(_cell(summa_mes_row, col)) if summa_mes_row else 0.0
            sheet_itogo = parse_num(_cell(itogo_row, col)) if itogo_row else 0.0
            sheet_ostalos = (parse_num(_cell(ostalos_row, col))
                             if ostalos_row is not None else sheet_obl)
            car = clean_name(_cell(avto_row, col)) if avto_row else ''
            daily_rate = parse_num(_cell(sutki_row, col)) if sutki_row else 0.0

            payments = []
            for pr, dr in enumerate(data_rows):
                raw = _cell(dr, col)
                cell0 = _cell(dr, 0)
                if isinstance(cell0, (date, datetime)):
                    pay_date = cell0 if isinstance(cell0, date) and not isinstance(cell0, datetime) else cell0.date()
                else:
                    pay_date = date(by, bm, min(pr + 1, days_in_month))

                if isinstance(raw, bool) or raw is None:
                    continue
                if isinstance(raw, (int, float)):
                    if raw > 0:
                        payments.append((pay_date, round(float(raw), 2)))
                elif isinstance(raw, str) and raw.strip():
                    # Текст в дневной ячейке — лист (SUM) его не считает. Если похоже
                    # на число, это вероятная незасчитанная оплата → аномалия.
                    pv = parse_num(raw)
                    if pv != 0:
                        anomalies.append({
                            'key': driver_cols[col]['key'],
                            'name': driver_cols[col]['name'],
                            'period': f'{by}-{bm:02d}',
                            'date': pay_date.isoformat(),
                            'raw': raw,
                            'parsed': round(pv, 2),
                        })

            # Пропускаем полностью пустые колонки (как parser).
            if sheet_obl == 0 and not payments and sheet_ostalos == 0:
                continue

            out.append({
                'key': driver_cols[col]['key'],
                'name': driver_cols[col]['name'],
                'car': car,
                'daily_rate': daily_rate,
                'year': by,
                'month': bm,
                'sheet_obligation': sheet_obl,
                'payments': payments,
                'sheet_itogo': sheet_itogo,
                'sheet_ostalos': sheet_ostalos,
            })

    return out, anomalies
