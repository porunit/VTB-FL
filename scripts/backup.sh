#!/usr/bin/env bash
# Ежедневный бэкап данных VTB-FL — три слоя сохранности:
#   1) .xlsx-снимок в формате исходной таблицы (привычный, открыть/проверить глазами)
#   2) pg_dump всей БД (полное восстановление одной командой) — если Postgres
#   3) офсайт-копия через rclone (S3/Яндекс.Диск/Google Drive) — если настроен
# Плюс ротация старых копий.
#
# Запуск (cron, ежедневно в 03:30):
#   30 3 * * *  DATABASE_URL=... BACKUP_DIR=/var/backups/vtbfl /path/scripts/backup.sh >> /var/log/vtbfl-backup.log 2>&1
#
# Переменные:
#   DATABASE_URL        — строка подключения (как у приложения), обязательна
#   BACKUP_DIR          — куда складывать (по умолч. /var/backups/vtbfl)
#   BACKUP_KEEP_DAYS    — сколько дней хранить (по умолч. 30)
#   RCLONE_REMOTE       — напр. "yandex:vtbfl-backups" для офсайт-копии (опц.)
set -euo pipefail

DATE="$(date +%F)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/vtbfl}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
HERE="$(cd "$(dirname "$0")" && pwd)"

: "${DATABASE_URL:?DATABASE_URL не задан}"
mkdir -p "$BACKUP_DIR"

xlsx="$BACKUP_DIR/export-$DATE.xlsx"
dump="$BACKUP_DIR/db-$DATE.sql.gz"

# 1) .xlsx-снимок (привычный формат)
( cd "$HERE/../backend" && python3 export_xlsx.py "$xlsx" )
echo "[backup] xlsx → $xlsx"

# 2) pg_dump всей БД (если Postgres и есть pg_dump)
pg_url="${DATABASE_URL/+psycopg2/}"   # pg_dump не понимает +psycopg2
if [[ "$pg_url" == postgres* ]] && command -v pg_dump >/dev/null 2>&1; then
  pg_dump "$pg_url" | gzip > "$dump"
  echo "[backup] pg_dump → $dump"
else
  echo "[backup] pg_dump пропущен (не Postgres или pg_dump недоступен)"
fi

# 3) ротация
find "$BACKUP_DIR" -name 'export-*.xlsx' -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true
find "$BACKUP_DIR" -name 'db-*.sql.gz'   -mtime "+$KEEP_DAYS" -delete 2>/dev/null || true

# 4) офсайт-копия (опц.) через rclone
if [[ -n "${RCLONE_REMOTE:-}" ]] && command -v rclone >/dev/null 2>&1; then
  rclone copy "$xlsx" "$RCLONE_REMOTE" && echo "[backup] офсайт xlsx → $RCLONE_REMOTE"
  [[ -f "$dump" ]] && rclone copy "$dump" "$RCLONE_REMOTE" && echo "[backup] офсайт dump → $RCLONE_REMOTE" || true
else
  echo "[backup] офсайт пропущен (RCLONE_REMOTE не задан или rclone недоступен)"
fi

echo "[backup] готово: $DATE"
