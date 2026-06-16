"""
Миграция таблицы платежей в БД (этап 0).

Поток: читаем сетку (xlsx-файл или живую Google-таблицу) → гранулярно выгружаем
записи (водитель × месяц) с дневными платежами → СВЕРКА против строк листа
«Итого»/«Осталось» → (опц.) загрузка в БД.

Сверка — «ворота»: грузить в БД имеет смысл только при нуле расхождений.
Запуск без --load ничего не пишет (безопасно гонять многократно).

Примеры:
  # только сверка из локального xlsx
  python migrate_sheet.py --xlsx "/path/Платежи клиентов 2026.xlsx"

  # сверка из живой Google-таблицы (нужен service-account, как у дашборда)
  python migrate_sheet.py --google

  # загрузить в БД (DATABASE_URL из окружения; для теста — sqlite по умолчанию)
  python migrate_sheet.py --xlsx "..." --load --create-all
"""

import argparse
import sys

from migration import corrections as corr
from migration import extract, reconcile


def read_grid(args):
    if args.xlsx:
        import xlsx_reader
        return xlsx_reader.read_grid(args.xlsx)
    if args.google:
        import sheet
        return sheet.fetch_grid()
    sys.exit('Укажите источник: --xlsx <путь> или --google')


def main():
    ap = argparse.ArgumentParser(description='Миграция таблицы платежей в БД (этап 0)')
    src = ap.add_argument_group('источник')
    src.add_argument('--xlsx', help='путь к .xlsx выгрузке таблицы')
    src.add_argument('--google', action='store_true', help='читать живую Google-таблицу')
    ap.add_argument('--load', action='store_true', help='загрузить в БД после успешной сверки')
    ap.add_argument('--create-all', action='store_true',
                    help='создать таблицы через metadata (для локального теста; в проде — alembic)')
    ap.add_argument('--force', action='store_true', help='грузить даже при расхождениях сверки')
    args = ap.parse_args()

    rows, formulas, tz = read_grid(args)
    records, anomalies = extract.extract_driver_months(rows, formulas, tz)
    ok, disc = reconcile.reconcile(records)

    print(reconcile.format_report(records, disc))
    applied = corr.effective(anomalies)
    if anomalies:
        fixed_keys = {(c['key'], f"{c['year']}-{c['month']:02d}", c['date']) for c in applied}
        print(f"\n⚠ Аномалии (текст вместо числа, лист их не считает) — {len(anomalies)}:")
        for a in anomalies:
            mark = '✓ учтётся (исправление)' if (a['key'], a['period'], a['date']) in fixed_keys \
                else '✗ НЕ учтено — добавьте в corrections.py или исправьте источник'
            print(f"   {a['name']} {a['period']} {a['date']}: {a['raw']!r} → {a['parsed']}  [{mark}]")
        print("   Исправьте ячейки в источнике (введите числом); список — docs/data-anomalies.md.")

    if not args.load:
        print("\n(пробный прогон — в БД ничего не записано; добавьте --load для загрузки)")
        return 0 if ok else 1

    if not ok and not args.force:
        print("\n✗ Загрузка отменена: есть расхождения сверки. Исправьте источник или --force.")
        return 1

    from db import Base, engine, get_session
    if args.create_all:
        import models  # noqa: F401 — регистрирует таблицы в metadata
        Base.metadata.create_all(engine)

    from migration import load as loader
    session = get_session()
    try:
        counts = loader.load(records, session, corrections=applied)
    finally:
        session.close()

    print("\nЗагружено в БД:")
    for k in ('drivers', 'cars', 'rentals', 'rental_months', 'payments'):
        print(f"   {k:<15} {counts.get(k, 0)}")
    if counts.get('corrections'):
        print(f"   {'(из них исправлений)':<15} {counts['corrections']} — учтены деньги, "
              "которые лист не считал")
    return 0


if __name__ == '__main__':
    sys.exit(main())
