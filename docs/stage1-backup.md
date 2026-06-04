# Сохранность данных — 3 слоя (этап 1)

Закрывает главный риск перехода с Google-таблицы: потерю данных. Три независимых
слоя, чтобы потеря была практически невозможна.

## Слой 1 — структурный (append-only)

Журнал `payments` только дописывается: платёж не удаляется и не правится, а
аннулируется (`voided_at`). Затереть/потерять запись нельзя в принципе, есть
полный аудит. Уже реализовано (миграция 0002).

## Слой 2 — ежедневные снимки на сервере

Скрипт `scripts/backup.sh` (cron, ежедневно) делает две копии:

| Файл | Что | Чем восстановить |
|---|---|---|
| `export-ГГГГ-ММ-ДД.xlsx` | снимок в **формате исходной таблицы** (месячные блоки) | открыть в Excel/Sheets; перезалить через `migrate_sheet.py` |
| `db-ГГГГ-ММ-ДД.sql.gz` | полный `pg_dump` всей БД | `gunzip -c db-….sql.gz \| psql "$DATABASE_URL"` |

xlsx-снимок — привычный и человекочитаемый (проверить глазами, заархивировать);
pg_dump — точное полное восстановление. Старые копии чистятся (`BACKUP_KEEP_DAYS`,
по умолчанию 30 дней).

`backend/export_xlsx.py` реконструирует исходную раскладку из Б\Д (Сумма/сутки,
сумма мес., Номер авто, дни, Итого, Осталось). Проверено round-trip: все 283
столбца (водитель×месяц) сходятся с БД до копейки.

## Слой 3 — офсайт-копия (вне сервера)

Если задан `RCLONE_REMOTE`, обе копии дублируются через
[rclone](https://rclone.org) в облако (S3 / Яндекс.Диск / Google Drive) — защита
даже при потере самого VPS. Настройка один раз: `rclone config`, затем
`RCLONE_REMOTE="yandex:vtbfl-backups"`.

## Установка (на сервере)

```bash
# зависимости для бэкапа на хосте
apt-get install -y postgresql-client       # даёт pg_dump
# (опц.) офсайт: curl https://rclone.org/install.sh | bash && rclone config

# cron — ежедневно в 03:30
crontab -e
30 3 * * *  DATABASE_URL="postgresql+psycopg2://user:pass@host:5432/vtbfl" \
            BACKUP_DIR=/var/backups/vtbfl \
            RCLONE_REMOTE="yandex:vtbfl-backups" \
            /opt/VTB-FL/scripts/backup.sh >> /var/log/vtbfl-backup.log 2>&1
```

Скрипт сам пропускает недоступные шаги (нет pg_dump → только xlsx; нет
RCLONE_REMOTE → только локально), поэтому безопасен в любой среде.

## Проверено

```
xlsx-снимок: 6 месяцев, 283 столбца == БД (0 расхождений)        ✓
pg_dump (Postgres 15): db-….sql.gz, COPY payments/rental_months/drivers ✓
ротация и пропуск недоступных шагов                              ✓
```

## Восстановление

- **Из xlsx**: `python migrate_sheet.py --xlsx export-….xlsx --load` (идемпотентно,
  со сверкой) — поднимет данные в чистую БД.
- **Из pg_dump**: `gunzip -c db-….sql.gz | psql "$PG_URL"` в пустую базу.
