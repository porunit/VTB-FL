# Схема БД и план миграции (Этапы 0–1)

> Фундамент для перехода с Google-таблицы (read-only) на Postgres с записью.
> Опирается на целевую модель из [ИКР.md](ИКР.md). Стек: Postgres + SQLAlchemy + Alembic
> (встраивается в существующий Python/FastAPI backend).

---

## 1. Обзор сущностей (ER)

```
users (operator / collector / owner)
   │ entered_by
   ▼
drivers ──< rentals >── cars
   │           │
   │           ├──< rental_months >──< payments        (деньги: вводит только оператор)
   │           │         │
   │           │         ├──< notes                     (заметки с датой)
   │           │         └──< collection_tasks          (полевой слой сборщика, деньги не двигает)
   │           │
   │           └──< documents                           (договор-основание)
   └──< documents
```

Ключевые принципы (из ИКР):
1. **Базовая единица учёта — `rental_months`** = пара (аренда × месяц). К ней привязаны деньги, причина неплатежа, заметки, задачи сбора.
2. **Деньги пишет только оператор** — через `payments.entered_by`. Поле `collector_id` в платеже информационное (кто физически принёс наличку), но оно НЕ источник истины.
3. **Полевой слой сборщика (`collection_tasks`) отделён от денег** — сборщик меняет `field_status`, баланс при этом не двигается.
4. **Договор (`documents`)** — однонаправленный поток: оператор грузит → сборщик видит как основание.

---

## 2. Соглашение о знаках (важно)

В Google-таблице обязательство хранится **отрицательным** (`сумма мес. = −99000`),
платежи положительные, `Осталось = обязательство + платежи`.

В БД делаем **чище**:
- `obligation` хранится **положительным** (99000 = «должен начислено за месяц»);
- `amount` платежа положительный;
- `balance = Σ payments − obligation` → **отрицательный баланс = долг**, ноль/плюс = месяц закрыт.

Маппинг при миграции: `obligation_db = −obligation_sheet`. Сверка: `balance_db` должен совпасть с `Осталось` из таблицы.

---

## 3. DDL (Postgres)

```sql
-- ========== ENUMS ==========
CREATE TYPE user_role         AS ENUM ('operator', 'collector', 'owner', 'admin');
CREATE TYPE payment_source    AS ENUM ('cash', 'buyout', 'withholding', 'return', 'other');
CREATE TYPE rmonth_status     AS ENUM ('open', 'closed', 'debt', 'frozen');
CREATE TYPE nonpayment_reason AS ENUM ('accident', 'illness', 'forgot', 'stalling', 'malicious', 'other');
CREATE TYPE field_status      AS ENUM ('pending', 'visited', 'promised', 'refused', 'absent');
CREATE TYPE document_type     AS ENUM ('contract', 'addendum', 'receipt', 'other');

-- ========== USERS ==========
CREATE TABLE users (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text        NOT NULL,
    role        user_role   NOT NULL,
    telegram_id bigint      UNIQUE,            -- для бота сборщика
    is_active   boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ========== DRIVERS ==========
CREATE TABLE drivers (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name       text        NOT NULL,
    normalized_name text        NOT NULL,      -- lower(trim(...)) для матчинга с таблицей
    phone           text,
    in_park         boolean     NOT NULL DEFAULT false,  -- пометка «в парке» (списание штрафов)
    is_active       boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (normalized_name)
);

-- ========== CARS ==========
CREATE TABLE cars (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plate      text        NOT NULL UNIQUE,    -- Т866НХ198
    model      text,
    is_active  boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ========== RENTALS (договор: водитель + машина + условия) ==========
CREATE TABLE rentals (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    driver_id    bigint        NOT NULL REFERENCES drivers(id),
    car_id       bigint        NOT NULL REFERENCES cars(id),
    daily_rate   numeric(12,2) NOT NULL,       -- Сумма/сутки (актуальная)
    start_date   date          NOT NULL DEFAULT current_date,
    end_date     date,                          -- NULL = активна
    is_active    boolean       NOT NULL DEFAULT true,
    created_at   timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX idx_rentals_driver ON rentals(driver_id);
CREATE INDEX idx_rentals_car    ON rentals(car_id);

-- ========== RENTAL_MONTHS (единица учёта: аренда × месяц) ==========
CREATE TABLE rental_months (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rental_id    bigint        NOT NULL REFERENCES rentals(id),
    driver_id    bigint        NOT NULL REFERENCES drivers(id),   -- денормализация для запросов
    car_id       bigint        NOT NULL REFERENCES cars(id),
    year         smallint      NOT NULL,
    month        smallint      NOT NULL CHECK (month BETWEEN 1 AND 12),
    obligation   numeric(12,2) NOT NULL,        -- положительное (99000)
    daily_rate   numeric(12,2) NOT NULL,        -- снимок ставки за этот месяц
    status       rmonth_status NOT NULL DEFAULT 'open',
    reason       nonpayment_reason,             -- почему не платит (NULL если платит)
    created_at   timestamptz   NOT NULL DEFAULT now(),
    UNIQUE (rental_id, year, month)
);
CREATE INDEX idx_rmonth_driver ON rental_months(driver_id, year, month);
CREATE INDEX idx_rmonth_period ON rental_months(year, month);

-- ========== PAYMENTS (журнал денег — пишет ТОЛЬКО оператор) ==========
CREATE TABLE payments (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rental_month_id bigint        NOT NULL REFERENCES rental_months(id),
    amount          numeric(12,2) NOT NULL CHECK (amount <> 0),
    paid_at         date          NOT NULL,      -- дата получения денег
    source          payment_source NOT NULL DEFAULT 'cash',
    entered_by      bigint        NOT NULL REFERENCES users(id),  -- оператор (источник истины)
    collector_id    bigint        REFERENCES users(id),           -- кто принёс наличку (информационно)
    note            text,
    created_at      timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX idx_payments_rmonth ON payments(rental_month_id);
CREATE INDEX idx_payments_date   ON payments(paid_at);

-- ========== DOCUMENTS (договор-основание) ==========
CREATE TABLE documents (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    driver_id           bigint        NOT NULL REFERENCES drivers(id),
    rental_id           bigint        REFERENCES rentals(id),
    type                document_type NOT NULL DEFAULT 'contract',
    file_url            text          NOT NULL,   -- путь к PDF/фото
    is_collection_basis boolean       NOT NULL DEFAULT true,  -- показывать сборщику
    uploaded_by         bigint        NOT NULL REFERENCES users(id),
    created_at          timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX idx_documents_driver ON documents(driver_id);

-- ========== NOTES (заметки с датой на месяц/водителя) ==========
CREATE TABLE notes (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rental_month_id bigint        REFERENCES rental_months(id),   -- NULL = заметка к водителю в целом
    driver_id       bigint        NOT NULL REFERENCES drivers(id),
    body            text          NOT NULL,
    created_by      bigint        NOT NULL REFERENCES users(id),
    created_at      timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX idx_notes_rmonth ON notes(rental_month_id);
CREATE INDEX idx_notes_driver ON notes(driver_id);

-- ========== COLLECTION_TASKS (полевой слой — деньги НЕ двигает) ==========
CREATE TABLE collection_tasks (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rental_month_id     bigint        NOT NULL REFERENCES rental_months(id),
    driver_id           bigint        NOT NULL REFERENCES drivers(id),
    assigned_collector  bigint        REFERENCES users(id),
    field_status        field_status  NOT NULL DEFAULT 'pending',
    promised_amount     numeric(12,2),
    promised_date       date,
    comment             text,
    updated_at          timestamptz   NOT NULL DEFAULT now(),
    created_at          timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX idx_ctasks_collector ON collection_tasks(assigned_collector, field_status);
CREATE INDEX idx_ctasks_rmonth    ON collection_tasks(rental_month_id);

-- ========== VIEW: баланс по месяцам (paid/balance — производные) ==========
CREATE VIEW v_rental_month_balance AS
SELECT
    rm.id              AS rental_month_id,
    rm.driver_id,
    rm.car_id,
    rm.year, rm.month,
    rm.obligation,
    COALESCE(SUM(p.amount) FILTER (WHERE p.source IN ('cash','buyout')), 0) AS paid,
    COALESCE(SUM(p.amount) FILTER (WHERE p.source IN ('cash','buyout')), 0) - rm.obligation AS balance,
    MAX(p.paid_at)     AS last_payment_date,
    rm.status, rm.reason
FROM rental_months rm
LEFT JOIN payments p ON p.rental_month_id = rm.id
GROUP BY rm.id;
```

---

## 4. Решения и обоснование

| Решение | Почему |
|---|---|
| `rentals` как отдельная сущность (договор) | водитель может сменить машину/ставку; договор-основание (`documents`) привязывается сюда; история аренд сохраняется |
| `rental_months` — ядро | ровно отражает месячные блоки таблицы; к месяцу крепятся причина, заметки, задачи сбора |
| `paid`/`balance` — во VIEW, не в колонках | единый источник истины — журнал `payments`; нельзя рассинхронить. Закрытые месяцы стабильны, т.к. платежи в них не меняются |
| `payments.entered_by` (оператор) ≠ `collector_id` | разделение полномочий: деньги вводит оператор, сборщик лишь принёс — для аналитики «дисциплина сборщика» |
| `collection_tasks` отдельно от `payments` | полевой статус (был/обещал/отказ) не двигает баланс — герметичность денежного контура |
| `obligation` положительное | чище, чем отрицательное в таблице; знак долга несёт `balance` |
| enum `reason` со `frozen`-статусом | «не грузить аварийного»: ставим reason=accident + status=frozen → месяц выпадает из давления, но долг виден |
| штрафы/удержания — НЕ в MVP | этапы 4–5; `payment_source` уже включает `withholding`/`return` для расширения без миграции |

---

## 5. План миграции из Google-таблицы

Переиспользуем логику `backend/parser.py` (он уже разбирает блоки). Скрипт `migrate_sheet.py` идемпотентный (upsert по натуральным ключам), чтобы гонять многократно до точной сверки.

**Алгоритм:** для каждого месячного блока, для каждой колонки-водителя:
1. `drivers` — upsert по `normalized_name = lower(trim(name))` (имена в таблице с хвостовыми пробелами: «Ребров », «Исмаилов » — тримим).
2. `cars` — upsert по `plate` из строки «Номер авто».
3. `rentals` — upsert по `(driver_id, car_id)`; `daily_rate` из «Сумма/сутки».
4. `rental_months` — upsert по `(rental_id, year, month)`; `obligation = −(сумма мес.)`.
5. Дневные ячейки `> 0` → `payments` (source=`cash`, `paid_at` = дата строки, `entered_by` = системный пользователь «migration»).
6. Колонка `ВЫКУП` → `payments` с `source=buyout`.

**Идентичность между месяцами:** водитель — по нормализованному имени, машина — по номеру. Если у водителя в разных месяцах разная машина → разные `rentals`, `rental_month` указывает на аренду, активную в этом месяце.

---

## 6. Стратегия сверки (gate перед переключением)

После прогона миграции автоматически сверяем БД против таблицы — **переключаемся на ввод в БД только когда расхождений ноль**:

| Проверка | Условие |
|---|---|
| Собрано за месяц | `Σ payments` по `rental_month` == строка `Итого` колонки |
| Остаток | `v_rental_month_balance.balance` == строка `Осталось` |
| Кол-во водителей | число `rental_months` за месяц == число колонок в блоке |
| Итог по парку | Σ `balance` по закрытым месяцам == `closedBalance` из текущего API |

Скрипт печатает таблицу расхождений (водитель, месяц, ожидание из листа, факт из БД, дельта). **Двойной период:** какое-то время читаем лист И пишем в БД параллельно; после нулевой сверки — оператор переходит на ввод в БД, лист замораживается.

---

## 7. Что дальше (после схемы)

1. SQLAlchemy-модели + Alembic baseline-миграция (этот DDL).
2. Скрипт `migrate_sheet.py` + отчёт сверки.
3. API оператора: `POST /payments`, `PATCH /rental_months/{id}` (reason/status), `POST /documents`.
4. Подключить существующий дашборд к БД вместо листа (модель ответа сохранить, чтобы фронт не переписывать).
