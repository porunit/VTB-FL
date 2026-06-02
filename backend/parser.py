"""
Парсер приватной Google-таблицы лизинговой компании — порт Code.gs на Python.

Таблица: вертикально сложенные блоки-месяцы. В каждом блоке строки:
  «Сумма/сутки» (старт блока), строка имён (месяц + ФИО по колонкам),
  «сумма мес.» (обязательство, отрицательное), «Номер авто», ежедневные платежи,
  «Итого», «Осталось» (= обязательство + оплата).

Вход parser.build_model():
  rows[r][c]     — значение ячейки: datetime.date для дат, float/str/bool или None;
  formulas[r][c] — строка-формула ('' если её нет);
  tz             — таймзона таблицы (строка, напр. 'Europe/Moscow').
Строки и формулы должны быть прямоугольными (одинаковая ширина).
"""

import re
import calendar
from datetime import date, datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

NBSP = ' '


# ---------------------------------------------------------------------------
# Утилиты парсинга
# ---------------------------------------------------------------------------

def norm(v):
    """Нормализация ярлыка: убрать nbsp, trim, нижний регистр, схлопнуть пробелы."""
    if v is None:
        return ''
    s = str(v).replace(NBSP, ' ').strip().lower()
    return re.sub(r'\s+', ' ', s)


def clean_name(v):
    """Отображаемое имя: nbsp→пробел, trim, схлопнуть двойные пробелы (регистр сохраняем)."""
    if v is None:
        return ''
    s = str(v).replace(NBSP, ' ').strip()
    return re.sub(r'\s+', ' ', s)


def name_key(v):
    return clean_name(v).lower()


def parse_num(v):
    """Число в обоих форматах: '-49 600,00' и '-99000'. Пусто/нечисло/дата → 0.0."""
    if v is None or v == '':
        return 0.0
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (date, datetime)):
        return 0.0
    s = str(v).replace(NBSP, ' ').strip()
    if s == '':
        return 0.0
    s = re.sub(r'\s', '', s)        # убрать разделители тысяч
    s = s.replace(',', '.')         # десятичная запятая → точка
    s = re.sub(r'[^0-9.\-]', '', s)  # выбросить ₽ и прочий мусор
    try:
        return float(s)
    except ValueError:
        return 0.0


MONTH_STEMS = [
    ('янв', 0), ('фев', 1), ('март', 2), ('мар', 2), ('апр', 3),
    ('май', 4), ('мая', 4), ('июн', 5), ('июл', 6), ('авг', 7),
    ('сен', 8), ('окт', 9), ('ноя', 10), ('дек', 11),
]


def month_from_label(s):
    n = norm(s)
    for stem, idx in MONTH_STEMS:
        if stem in n:
            return idx
    return -1


def is_month_name(s):  return month_from_label(s) >= 0
def is_sutki(s):       return 'сутки' in s
def is_summa_mes(s):   return 'сумма мес' in s
def is_nomer_avto(s):  return 'номер авто' in s or 'номер машины' in s
def is_itogo(s):       return 'итого' in s
def is_ostalos(s):     return 'осталось' in s or 'остаток' in s
def is_vykup(s):       return 'выкуп' in s


def find_total_col(summa_mes_row):
    """Индекс колонки-итога: в строке 'сумма мес.' агрегат кратно больше любого
    отдельного обязательства. None (≈ Infinity), если явного итога нет."""
    if not summa_mes_row:
        return None
    best, best_val, second = -1, 0.0, 0.0
    for c, cellv in enumerate(summa_mes_row):
        v = abs(parse_num(cellv))
        if v > best_val:
            second = best_val
            best_val = v
            best = c
        elif v > second:
            second = v
    if best >= 0 and second > 0 and best_val >= 2 * second:
        return best
    return None


def col_letters_to_index(letters):
    """A1-буквы → 0-индексный номер. 'A'→0, 'BA'→52, 'BD'→55."""
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)  # 'A' = 65
    return idx - 1


def formula_col_range(formula):
    """'=SUM(B39:BA39)' → (start, end) 0-индексные. None, если не распознано."""
    if not formula:
        return None
    m = re.search(r'([A-Z]+)\d+\s*:\s*([A-Z]+)\d+', str(formula))
    if not m:
        return None
    a = col_letters_to_index(m.group(1))
    b = col_letters_to_index(m.group(2))
    return (min(a, b), max(a, b))


def row_type(row):
    for cellv in row:
        s = norm(cellv)
        if not s:
            continue
        if is_sutki(s):      return 'sutki'
        if is_summa_mes(s):  return 'summaMes'
        if is_nomer_avto(s): return 'avto'
        if is_ostalos(s):    return 'ostalos'
        if is_itogo(s):      return 'itogo'
    return 'data'


def round2(n):
    return round(n * 100) / 100


def pad2(n):
    return ('0' if n < 10 else '') + str(n)


def _cell(row, c):
    return row[c] if 0 <= c < len(row) else None


# ---------------------------------------------------------------------------
# Построение модели
# ---------------------------------------------------------------------------

def _tzinfo(tz):
    """ZoneInfo по имени; при отсутствии базы tz (напр. Windows без tzdata) —
    фиксированный сдвиг для Москвы (UTC+3), иначе UTC."""
    try:
        return ZoneInfo(tz)
    except Exception:
        return dt_timezone(timedelta(hours=3)) if tz == 'Europe/Moscow' else dt_timezone.utc


def build_model(rows, formulas, tz='Europe/Moscow'):
    now = datetime.now(_tzinfo(tz))
    today = {'y': now.year, 'm': now.month - 1, 'd': now.day}
    today_ms = int(datetime(today['y'], today['m'] + 1, today['d'],
                            tzinfo=dt_timezone.utc).timestamp() * 1000)

    # 1) Старты блоков (строки «Сумма/сутки»).
    block_starts = [i for i, r in enumerate(rows) if row_type(r) == 'sutki']

    months = []
    records = []
    buyout_by_month = []

    for b in range(len(block_starts)):
        start = block_starts[b]
        end = block_starts[b + 1] if b + 1 < len(block_starts) else len(rows)
        names_row = rows[start + 1] if start + 1 < len(rows) else []

        # Колонки водителей: непустые ячейки строки имён, кроме месяца/ВЫКУП/сутки.
        driver_cols = {}
        vykup_cols = []
        month_label = ''
        for c, raw in enumerate(names_row):
            if raw is None or str(raw).strip() == '':
                continue
            nm = norm(raw)
            if is_month_name(nm):
                if not month_label:
                    month_label = clean_name(raw)
                continue
            if is_vykup(nm):
                vykup_cols.append(c)
                continue
            if is_sutki(nm):
                continue
            driver_cols[c] = {'name': clean_name(raw), 'key': name_key(raw)}

        # Якорные строки внутри блока.
        summa_mes_row = avto_row = itogo_row = ostalos_row = None
        summa_mes_idx = avto_idx = itogo_idx = ostalos_idx = -1
        for r in range(start + 1, end):
            t = row_type(rows[r])
            if t == 'summaMes' and summa_mes_row is None:
                summa_mes_row, summa_mes_idx = rows[r], r
            elif t == 'avto' and avto_row is None:
                avto_row, avto_idx = rows[r], r
            elif t == 'itogo' and itogo_row is None:
                itogo_row, itogo_idx = rows[r], r
            elif t == 'ostalos' and ostalos_row is None:
                ostalos_row, ostalos_idx = rows[r], r

        # Ежедневные строки = data-строки между «Номер авто» и «Итого».
        day_start = avto_idx + 1 if avto_idx >= 0 else start + 2
        day_end = itogo_idx if itogo_idx >= 0 else end
        data_rows = []
        for d in range(day_start, day_end):
            if d < 0 or d >= len(rows):
                continue
            if row_type(rows[d]) == 'data':
                data_rows.append(rows[d])

        # Граница колонок водителей = диапазон официальной формулы «Осталось».
        # Лист может НЕ включать крайние правые столбцы в месячный итог.
        total_col = find_total_col(summa_mes_row)
        col_range = None
        if ostalos_row is not None and total_col is not None and ostalos_idx >= 0:
            col_range = formula_col_range(_cell(formulas[ostalos_idx], total_col)
                                          if ostalos_idx < len(formulas) else None)
        if col_range:
            lo, hi = col_range
            for dck in list(driver_cols.keys()):
                if dck < lo or dck > hi or dck >= total_col:
                    del driver_cols[dck]
        elif total_col is not None:
            for dck in list(driver_cols.keys()):
                if dck >= total_col:
                    del driver_cols[dck]

        # Месяц/год блока — из ячеек-дат ежедневных строк (надёжнее ярлыка).
        by, bm = -1, -1
        for dr in data_rows:
            cell0 = _cell(dr, 0)
            if isinstance(cell0, (date, datetime)):
                by, bm = cell0.year, cell0.month - 1
                break
        if bm < 0:
            bm = month_from_label(month_label)
            ym = re.search(r'(20\d{2})', str(month_label))
            by = int(ym.group(1)) if ym else today['y']
            if bm < 0:
                bm = today['m']

        days_in_month = calendar.monthrange(by, bm + 1)[1]
        is_current = (by == today['y'] and bm == today['m'])
        days_elapsed = min(today['d'], days_in_month) if is_current else days_in_month

        month_index = len(months)
        months.append({
            'index': month_index,
            'label': month_label or f'{bm + 1}.{by}',
            'year': by, 'month': bm,
            'isCurrent': is_current,
            'daysInMonth': days_in_month,
            'daysElapsed': days_elapsed,
            'sortKey': by * 12 + bm,
        })

        # ВЫКУП — отдельный поток.
        vykup_total = 0.0
        for vc in vykup_cols:
            for dr in data_rows:
                vykup_total += max(0.0, parse_num(_cell(dr, vc)))
        if vykup_total:
            buyout_by_month.append({'monthIndex': month_index, 'total': vykup_total})

        # Запись по каждому водителю.
        has_ostalos = ostalos_row is not None
        for col in sorted(driver_cols.keys()):
            obligation = parse_num(_cell(summa_mes_row, col)) if summa_mes_row else 0.0

            paid = 0.0
            last_day = 0
            pay_days = 0
            for pr, dr in enumerate(data_rows):
                val = parse_num(_cell(dr, col))
                if val > 0:
                    paid += val
                    pay_days += 1
                    dc = _cell(dr, 0)
                    day_num = dc.day if isinstance(dc, (date, datetime)) else (pr + 1)
                    if day_num > last_day:
                        last_day = day_num
            itogo_paid = parse_num(_cell(itogo_row, col)) if itogo_row else 0.0
            if itogo_paid > 0:
                paid = itogo_paid  # «Итого» авторитетнее, если есть

            remaining = parse_num(_cell(ostalos_row, col)) if has_ostalos else (obligation + paid)

            active = obligation != 0
            if not obligation and not paid and not remaining:
                continue

            car = clean_name(_cell(avto_row, col)) if avto_row else ''

            records.append({
                'key': driver_cols[col]['key'],
                'name': driver_cols[col]['name'],
                'car': car,
                'monthIndex': month_index,
                'obligation': obligation,
                'paid': paid,
                'remaining': remaining,
                'active': active,
                'lastPaymentDay': last_day,
                'paymentDays': pay_days,
            })

    # «Текущий» месяц = самый поздний блок по дате, не календарное «сегодня».
    latest = None
    for m in months:
        if latest is None or m['sortKey'] > latest['sortKey']:
            latest = m
    for mm in months:
        mm['isCurrent'] = latest is not None and mm['index'] == latest['index']
        if mm['isCurrent']:
            within = (mm['year'] == today['y'] and mm['month'] == today['m'])
            mm['daysElapsed'] = min(today['d'], mm['daysInMonth']) if within else mm['daysInMonth']
        else:
            mm['daysElapsed'] = mm['daysInMonth']

    return aggregate(records, months, buyout_by_month, today, today_ms, tz)


# ---------------------------------------------------------------------------
# Агрегация в финальную модель
# ---------------------------------------------------------------------------

def aggregate(records, months, buyout_by_month, today, today_ms, tz):
    by_key = {}
    for rec in records:
        grp = by_key.get(rec['key'])
        if grp is None:
            grp = by_key[rec['key']] = {'key': rec['key'], 'name': rec['name'], 'recs': []}
        grp['recs'].append(rec)
        grp['name'] = rec['name']  # самое свежее имя

    months_by_sort = sorted(months, key=lambda m: m['sortKey'])

    drivers = []
    aging = {'current': 0.0, 'm1': 0.0, 'm23': 0.0, 'm3plus': 0.0}
    net_balance = gross_debt = debt_over_2m = 0.0
    debtors_count = 0
    cur_ob_sum = cur_paid_sum = 0.0

    current_month = next((m for m in months if m['isCurrent']), None)

    for grp in by_key.values():
        recs_by_month = {r['monthIndex']: r for r in grp['recs']}
        car = ''
        monthly = []
        trend = []
        closed_balance = total_obligation = total_paid = 0.0
        cur = None

        for mo in months_by_sort:
            rc = recs_by_month.get(mo['index'])
            if rc is None:
                continue
            if rc['car']:
                car = rc['car']
            collection = min(1.0, rc['paid'] / abs(rc['obligation'])) if rc['obligation'] else 0.0
            entry = {
                'label': mo['label'], 'year': mo['year'], 'month': mo['month'],
                'isCurrent': mo['isCurrent'],
                'obligation': rc['obligation'], 'paid': rc['paid'], 'remaining': rc['remaining'],
                'collection': collection, 'car': rc['car'], 'lastPaymentDay': rc['lastPaymentDay'],
            }
            monthly.append(entry)
            trend.append({'label': mo['label'], 'remaining': rc['remaining'], 'isCurrent': mo['isCurrent']})
            if mo['isCurrent']:
                cur = entry
            else:
                closed_balance += rc['remaining']
                total_obligation += rc['obligation']
                total_paid += rc['paid']

        # Глубина просрочки: подряд идущие закрытые месяцы с долгом (с конца).
        closed_chrono = [e for e in monthly if not e['isCurrent']]
        months_overdue = 0
        for e in reversed(closed_chrono):
            if e['remaining'] < -0.5:
                months_overdue += 1
            else:
                break

        # Текущий месяц — pro-rata прогноз, в просрочку не пишем.
        current_obligation = cur['obligation'] if cur else 0.0
        current_paid = cur['paid'] if cur else 0.0
        current_remaining = cur['remaining'] if cur else 0.0
        prorata_expected = current_collection = 0.0
        if cur and current_month:
            prorata_expected = abs(current_obligation) * (current_month['daysElapsed'] / current_month['daysInMonth'])
            current_collection = min(1.0, current_paid / prorata_expected) if prorata_expected else 0.0
            cur_ob_sum += abs(current_obligation)
            cur_paid_sum += current_paid

        # Дней без оплаты — от последней фактической оплаты до сегодня.
        last_payment_date = None
        days_without_payment = None
        for mo in reversed(months_by_sort):
            rc = recs_by_month.get(mo['index'])
            if rc and rc['lastPaymentDay'] > 0:
                last_payment_date = f"{mo['year']:04d}-{pad2(mo['month'] + 1)}-{pad2(rc['lastPaymentDay'])}"
                lp_ms = int(datetime(mo['year'], mo['month'] + 1, rc['lastPaymentDay'],
                                     tzinfo=dt_timezone.utc).timestamp() * 1000)
                days_without_payment = max(0, round((today_ms - lp_ms) / 86400000))
                break

        if closed_chrono:
            avg_obligation = sum(abs(e['obligation']) for e in closed_chrono) / len(closed_chrono)
        else:
            avg_obligation = abs(current_obligation)

        # Светофор статуса.
        if closed_balance >= -0.5:
            status = 'green'
        elif months_overdue >= 2 or abs(closed_balance) > avg_obligation:
            status = 'red'
        else:
            status = 'yellow'

        # Bucket старения.
        if closed_balance >= -0.5 or months_overdue == 0:
            bucket = 'current'
        elif months_overdue == 1:
            bucket = 'm1'
        elif months_overdue <= 3:
            bucket = 'm23'
        else:
            bucket = 'm3plus'

        debt = min(0.0, closed_balance)
        if closed_balance < -0.5:
            debtors_count += 1
            gross_debt += -debt
            aging[bucket] += -debt
            if months_overdue >= 2:
                debt_over_2m += -debt
        net_balance += closed_balance

        collection_rate = min(1.0, total_paid / abs(total_obligation)) if total_obligation else 0.0

        drivers.append({
            'key': grp['key'], 'name': grp['name'], 'car': car,
            'closedBalance': round2(closed_balance),
            'currentObligation': round2(current_obligation),
            'currentPaid': round2(current_paid),
            'currentRemaining': round2(current_remaining),
            'prorataExpected': round2(prorata_expected),
            'currentCollection': round2(current_collection),
            'totalObligation': round2(total_obligation),
            'totalPaid': round2(total_paid),
            'collectionRate': round2(collection_rate),
            'monthsOverdue': months_overdue,
            'daysWithoutPayment': days_without_payment,
            'lastPaymentDate': last_payment_date,
            'status': status,
            'agingBucket': bucket,
            'trend': trend,
            'monthly': monthly,
        })

    drivers.sort(key=lambda d: d['closedBalance'])  # худшие сверху

    totals = {
        'netBalance': round2(net_balance),
        'totalDebt': round2(gross_debt),
        'debtorsCount': debtors_count,
        'avgDebt': round2(gross_debt / debtors_count) if debtors_count else 0,
        'collectionRateMonth': round2(min(1.0, cur_paid_sum / cur_ob_sum)) if cur_ob_sum else 0,
        'debtOver2m': round2(debt_over_2m),
        'aging': {
            'current': round2(aging['current']),
            'm1': round2(aging['m1']),
            'm23': round2(aging['m23']),
            'm3plus': round2(aging['m3plus']),
        },
    }

    return {
        'generatedAt': datetime.now(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'today': f"{today['y']:04d}-{pad2(today['m'] + 1)}-{pad2(today['d'])}",
        'timezone': tz,
        'months': months,
        'currentMonthLabel': current_month['label'] if current_month else None,
        'totals': totals,
        'drivers': drivers,
        'buyoutByMonth': buyout_by_month,
    }
