# Этап 1 — дашборд на БД + API оператора (реализовано)

Надстройка над этапом 0: дашборд может читать из БД, а оператор — вводить деньги
и помечать месяцы. Опирается на схему [db-schema.md](db-schema.md) и модель
аналитики [analytics.md](analytics.md).

## 1. Переключатель источника дашборда

`MODEL_SOURCE` (env): `sheet` (Google-таблица, по умолчанию) или `db` (Postgres).
Формат `/api/model` одинаковый — фронт не меняется.

- `backend/db_model.py` строит модель из БД, переиспользуя `parser.aggregate`
  (из БД восстанавливаются только `records`/`months`).
- В режиме `db` Google-библиотеки и креды не нужны (импорт `sheet` ленивый).

Проверено: модель из БД совпадает с моделью из листа по всем 59 водителям, кроме
Каменского (БД корректнее на 52 250 — учтено исправление); `debtorsCount` и
`debtOver2m` идентичны.

## 2. API оператора (`backend/operator_api.py`)

Единственная точка ввода денег. Включается, только если задан `DATABASE_URL`.
Под той же защитой (Basic Auth), что и дашборд.

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/api/payments` | зафиксировать платёж (получив наличку) |
| `GET` | `/api/rental_months/{id}` | состояние месяца (obligation/paid/balance/status/reason) |
| `PATCH` | `/api/rental_months/{id}` | причина неплатежа / статус месяца (заморозка) |

**POST /api/payments** — тело: `amount`, `paid_at`, и `rental_month_id` ЛИБО
(`driver_id` + `year` + `month`); опц. `source` (cash/buyout/…), `collector_id`
(кто принёс наличку — информационно), `note`. Ответ включает пересчитанные
`month_paid` / `month_balance`.

**PATCH /api/rental_months/{id}** — `reason` (accident/illness/forgot/stalling/
malicious/other), `status` (open/closed/debt/frozen), `clear_reason`. Пример
«не грузить аварийного»: `{"reason":"accident","status":"frozen"}`.

Баланс нигде не хранится — всегда пересчитывается из журнала `payments`.
`entered_by` — оператор (пока дефолтный пользователь `operator`; реальная
многопользовательская аутентификация — позже).

## Проверено (TestClient, SQLite)

```
POST платёж 30000 → balance −165100 → −135100
POST по driver+период 20000 → −115100
PATCH заморозка (accident) → status=frozen, reason=accident
amount=0 → 422 · несуществующий месяц → 404
```

Прогнано и против Postgres 15 на этапе 0 (нативные enum, CHECK-ограничения).

## Что НЕ входит (следующие шаги)

- Отображение причины/статуса (frozen) на дашборде — стадия аналитики
  (причина как измерение-фильтр, см. analytics.md).
- UI оператора (сейчас только API).
- Загрузка договоров (`documents`) и бот сборщика — этап 2.
- Реальная многопользовательская аутентификация операторов.
