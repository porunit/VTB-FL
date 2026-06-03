#!/usr/bin/env bash
# Разовая подготовка свежего сервера (Ubuntu 22.04/24.04) под дашборд.
# Идемпотентно: можно запускать повторно.
#
# Использование:
#   sudo bash server-bootstrap.sh
#
# После него ОБЯЗАТЕЛЬНО положить два файла (они в .gitignore, в репозитории их нет):
#   /opt/VTB-FL/backend/service-account.json   — ключ service-аккаунта Google
#   /opt/VTB-FL/.env                            — настройки (см. .env.deploy.example)
# затем поднять:  cd /opt/VTB-FL && docker compose up -d --build

set -euo pipefail

REPO_URL="https://github.com/porunit/VTB-FL.git"
APP_DIR="/opt/VTB-FL"

echo "==> Обновление пакетов"
apt-get update -y

echo "==> Установка git"
command -v git >/dev/null 2>&1 || apt-get install -y git

echo "==> Установка Docker (если нет)"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> Клонирование/обновление репозитория в ${APP_DIR}"
if [ -d "${APP_DIR}/.git" ]; then
  git -C "${APP_DIR}" fetch --all --prune
  git -C "${APP_DIR}" reset --hard origin/main
else
  git clone "${REPO_URL}" "${APP_DIR}"
fi

echo "==> Открытие портов (ufw, если установлен)"
if command -v ufw >/dev/null 2>&1; then
  ufw allow 22/tcp  || true
  ufw allow 80/tcp  || true
  ufw allow 443/tcp || true
  yes | ufw enable   || true
fi

echo
echo "================ ДАЛЬШЕ ВРУЧНУЮ ================"
echo "1) Положить ключ:   ${APP_DIR}/backend/service-account.json"
echo "2) Создать .env:    cp ${APP_DIR}/.env.deploy.example ${APP_DIR}/.env && nano ${APP_DIR}/.env"
echo "      DOMAIN=<IP-через-точки>.sslip.io   (напр. 203.0.113.5.sslip.io)"
echo "      DASH_USER=...  DASH_PASS=..."
echo "3) Поднять:         cd ${APP_DIR} && docker compose up -d --build"
echo "==============================================="
