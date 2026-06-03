"""
Аудит данных таблицы: ищет «оплошности оператора» — значения, которые ДОЛЖНЫ быть
числом, но введены текстом (напр. '52 250' с пробелом). Формула SUM/ссылки листа
такой текст не считают, поэтому деньги теряются из учёта.

Сканируем во всех месячных блоках по колонкам водителей все ЧИСЛОВЫЕ строки:
  - дневные платежи  (потерянная оплата — критично),
  - «сумма мес.» / «Итого» / «Осталось» / «Сумма/сутки».
А также отмечаем отрицательные дневные значения (тоже вероятная ошибка ввода).

Выдаёт список находок с адресом ячейки (как в Excel), периодом, водителем и сутью.
"""

from datetime import date, datetime

from openpyxl.utils import get_column_letter

from parser import (
    norm, clean_name, name_key, parse_num, row_type,
    is_month_name, is_vykup, is_sutki, month_from_label,
    find_total_col, formula_col_range, _cell,
)


def _addr(r_idx, c_idx):
    """Индексы в rows (0-based) → адрес ячейки Excel (1-based), напр. 'AW34'."""
    return f"{get_column_letter(c_idx + 1)}{r_idx + 1}"


def _looks_numeric_text(v):
    """True, если ячейка — строка, похожая на число (после очистки != 0)."""
    return isinstance(v, str) and v.strip() != '' and parse_num(v) != 0


def audit(rows, formulas, tz='Europe/Moscow'):
    """Возвращает список находок (dict)."""
    findings = []
    starts = [i for i, r in enumerate(rows) if row_type(r) == 'sutki']

    for b, start in enumerate(starts):
        end = starts[b + 1] if b + 1 < len(starts) else len(rows)
        sutki_row, sutki_idx = rows[start], start
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
            driver_cols[c] = clean_name(raw)

        summa_mes_row = itogo_row = ostalos_row = None
        summa_mes_idx = avto_idx = itogo_idx = ostalos_idx = -1
        for r in range(start + 1, end):
            t = row_type(rows[r])
            if t == 'summaMes' and summa_mes_row is None:
                summa_mes_row, summa_mes_idx = rows[r], r
            elif t == 'avto' and avto_idx < 0:
                avto_idx = r
            elif t == 'itogo' and itogo_row is None:
                itogo_row, itogo_idx = rows[r], r
            elif t == 'ostalos' and ostalos_row is None:
                ostalos_row, ostalos_idx = rows[r], r

        day_start = avto_idx + 1 if avto_idx >= 0 else start + 2
        day_end = itogo_idx if itogo_idx >= 0 else end
        data_idx = [d for d in range(day_start, day_end)
                    if 0 <= d < len(rows) and row_type(rows[d]) == 'data']

        # Граница колонок водителей (как в extract/parser).
        total_col = find_total_col(summa_mes_row)
        col_range = None
        if ostalos_row is not None and total_col is not None and ostalos_idx >= 0:
            col_range = formula_col_range(
                _cell(formulas[ostalos_idx], total_col) if ostalos_idx < len(formulas) else None)
        if col_range:
            lo, hi = col_range
            driver_cols = {c: n for c, n in driver_cols.items() if lo <= c <= hi and c < total_col}
        elif total_col is not None:
            driver_cols = {c: n for c, n in driver_cols.items() if c < total_col}

        # Период блока.
        by, bm = -1, -1
        for d in data_idx:
            c0 = _cell(rows[d], 0)
            if isinstance(c0, (date, datetime)):
                by, bm = c0.year, c0.month
                break
        if bm < 0:
            bm0 = month_from_label(month_label)
            bm = (bm0 + 1) if bm0 >= 0 else 0
            by = 0
        period = f"{by}-{bm:02d}" if by else (month_label or f"блок {b + 1}")

        def add(kind, r_idx, c_idx, name, raw, extra=''):
            findings.append({
                'kind': kind,
                'period': period,
                'driver': name,
                'cell': _addr(r_idx, c_idx),
                'raw': raw,
                'parsed': round(parse_num(raw), 2) if isinstance(raw, str) else raw,
                'extra': extra,
            })

        for c, name in driver_cols.items():
            # Дневные платежи.
            for d in data_idx:
                v = _cell(rows[d], c)
                if _looks_numeric_text(v):
                    c0 = _cell(rows[d], 0)
                    day = c0.isoformat() if isinstance(c0, (date, datetime)) else ''
                    add('текст-в-платеже', d, c, name, v, day)
                elif isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
                    c0 = _cell(rows[d], 0)
                    day = c0.isoformat() if isinstance(c0, (date, datetime)) else ''
                    add('отрицательный платёж', d, c, name, v, day)
            # Числовые строки-якоря.
            if summa_mes_row is not None and _looks_numeric_text(_cell(summa_mes_row, c)):
                add('текст-в-обязательстве', summa_mes_idx, c, name, _cell(summa_mes_row, c))
            if itogo_row is not None and _looks_numeric_text(_cell(itogo_row, c)):
                add('текст-в-итого', itogo_idx, c, name, _cell(itogo_row, c))
            if ostalos_row is not None and _looks_numeric_text(_cell(ostalos_row, c)):
                add('текст-в-осталось', ostalos_idx, c, name, _cell(ostalos_row, c))
            if sutki_row is not None and _looks_numeric_text(_cell(sutki_row, c)):
                add('текст-в-ставке', sutki_idx, c, name, _cell(sutki_row, c))

    return findings
