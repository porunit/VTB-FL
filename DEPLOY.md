# Деплой на Vultr с авто-обновлением

Архитектура CI/CD: **мерж PR в `main` → GitHub Actions заходит по SSH на сервер Vultr → `git pull` + `docker compose up -d --build`.** Сервис работает за Caddy с автоматическим HTTPS.

```
PR → merge в main ──> GitHub Actions (.github/workflows/deploy.yml)
                          │ ssh
                          ▼
                  Vultr VPS: /opt/VTB-FL
                  git reset --hard origin/main
                  docker compose up -d --build
                  ├─ app   (FastAPI + дашборд, :8080 внутри)
                  └─ caddy (:80/:443, авто-HTTPS)
```

## 1. Создать сервер (Vultr)
- Cloud Compute, **Ubuntu 24.04**, минимальный план (1 vCPU / 1 GB).
- Записать **IP**. Дать доступ по SSH (root + пароль или ключ).

## 2. Разовая подготовка сервера
```bash
ssh root@IP
curl -fsSL https://raw.githubusercontent.com/porunit/VTB-FL/main/scripts/server-bootstrap.sh -o b.sh
sudo bash b.sh
```
Скрипт ставит Docker/git, клонирует репо в `/opt/VTB-FL`, открывает порты 22/80/443.

Затем положить секреты (их нет в репозитории — в `.gitignore`):
```bash
# 1) ключ service-аккаунта
nano /opt/VTB-FL/backend/service-account.json     # вставить JSON-ключ

# 2) переменные окружения
cp /opt/VTB-FL/.env.deploy.example /opt/VTB-FL/.env
nano /opt/VTB-FL/.env
#   DOMAIN=<IP-через-точки>.sslip.io   напр. 203.0.113.5.sslip.io
#   DASH_USER=mozen   DASH_PASS=<пароль>

# 3) первый запуск
cd /opt/VTB-FL && docker compose up -d --build
```
Проверка: открыть `https://<IP>.sslip.io` (спросит логин/пароль).

## 3. Ключ для GitHub Actions → сервер
Сгенерировать отдельный SSH-ключ для CI и разрешить вход по нему:
```bash
ssh-keygen -t ed25519 -f ci_key -N "" -C "github-actions"
cat ci_key.pub >> ~/.ssh/authorized_keys     # на сервере, для пользователя деплоя
cat ci_key                                   # приватный ключ → в секрет VULTR_SSH_KEY
```

## 4. Секреты репозитория (Settings → Secrets and variables → Actions)
| Секрет | Значение |
|---|---|
| `VULTR_HOST` | IP сервера |
| `VULTR_USER` | `root` (или пользователь деплоя) |
| `VULTR_SSH_KEY` | приватный ключ `ci_key` (целиком) |
| `VULTR_SSH_PORT` | `22` (необязательно) |

## 5. Включить автодеплой
Смёржить PR в `main` — workflow запустится сам. Ручной прогон: вкладка **Actions → Deploy to Vultr → Run workflow**.

## Полезное (на сервере)
```bash
cd /opt/VTB-FL
docker compose logs -f        # логи
docker compose ps             # статус
docker compose restart        # перезапуск
docker compose down           # остановить
```

## Безопасность
- `service-account.json`, `.env`, `.venv` — в `.gitignore`, в образ не попадают (`.dockerignore`), монтируются в рантайме.
- Дашборд закрыт Basic Auth (`DASH_USER`/`DASH_PASS`), доступ только по HTTPS.
