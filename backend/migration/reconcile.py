"""
Сверка гранулярной выгрузки против собственных строк листа «Итого»/«Осталось».

Это «ворота» перед переключением на ввод в БД: миграция считается корректной,
только когда расхождений ноль. Сверяем по каждой записи (водитель × месяц):

  paid_computed  = Σ дневных платежей   ←→  лист «Итого»
  balance        = sheet_obligation + paid_computed   ←→  лист «Осталось»

(в листе обязательство отрицательное, платежи положительные, поэтому
 balance = obligation + paid — как и определяет сам лист).
"""

EPS = 0.5  # копеечные расхождения округления игнорируем


def reconcile(records):
    """Возвращает (ok, discrepancies). discrepancies — список словарей."""
    discrepancies = []
    for rec in records:
        paid = round(sum(amount for _, amount in rec['payments']), 2)
        balance = round(rec['sheet_obligation'] + paid, 2)

        d_paid = round(paid - rec['sheet_itogo'], 2)
        d_balance = round(balance - rec['sheet_ostalos'], 2)

        if abs(d_paid) > EPS or abs(d_balance) > EPS:
            discrepancies.append({
                'key': rec['key'],
                'name': rec['name'],
                'period': f"{rec['year']}-{rec['month']:02d}",
                'paid_computed': paid,
                'sheet_itogo': rec['sheet_itogo'],
                'delta_paid': d_paid,
                'balance_computed': balance,
                'sheet_ostalos': rec['sheet_ostalos'],
                'delta_balance': d_balance,
            })

    return (len(discrepancies) == 0, discrepancies)


def format_report(records, discrepancies):
    lines = []
    lines.append(f"Записей (водитель×месяц): {len(records)}")
    total_payments = sum(len(r['payments']) for r in records)
    lines.append(f"Дневных платежей всего:   {total_payments}")
    lines.append(f"Расхождений:              {len(discrepancies)}")
    if discrepancies:
        lines.append("")
        lines.append(f"{'Водитель':<22}{'Период':<10}{'Δ оплата':>12}{'Δ остаток':>12}")
        lines.append("-" * 56)
        for d in discrepancies[:50]:
            lines.append(f"{d['name'][:21]:<22}{d['period']:<10}"
                         f"{d['delta_paid']:>12.2f}{d['delta_balance']:>12.2f}")
        if len(discrepancies) > 50:
            lines.append(f"... и ещё {len(discrepancies) - 50}")
    else:
        lines.append("✓ Сверка пройдена: расхождений нет.")
    return "\n".join(lines)
