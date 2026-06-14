# TCP — Trading Central Panel: Database Design

> **Document scope.** End-to-end T-SQL database design for the TCP — Trading Central Panel platform, targeting **Azure SQL Database — Free Offer** (Serverless General Purpose, 1 vCore, auto-pause after 60 minutes, 32 GB included).
> **Author:** TODO
> **Advisor:** TODO
> **Stage:** Etapa 1 — Database Design
> **Predecessor:** `01_business_requirements.md`
> **Successor:** `03_architecture.md`, `db/migrations/V001__init.sql`

---

## 1. Overview

TCP's data layer is a single Azure SQL Database modelled as a **light star schema**: one transactional fact table (`fact_Trades`) surrounded by a small set of conformed dimensions (`dim_*`) and one slowly-changing configuration table (`config_Capital`). The physical model is **OLTP-style** (3NF where it matters, narrow rows, FK-enforced referential integrity, append-only fact writes from the daily generator) while a set of layered **reporting views** (`v_*`) provides the **OLAP-style** denormalised projection consumed by PowerBI and the `/api/ask` AI assistant.

The design is sized for a 32-employee firm producing roughly 60 000 fact rows per year — well below any threshold that would justify Hyperscale, In-Memory OLTP, or table partitioning. The whole schema fits comfortably in the Free Offer's 32 GB storage envelope and the 100 000 vCore-seconds/month compute budget, with the database expected to spend most of the day **auto-paused**.

---

## 2. Entity-relationship overview

See `docs/diagrams/erd.mmd` for the visual entity-relationship diagram.

### 2.1. Entities

- **`dim_Companies`** — the single legal entity (TCP Capital Management SRL).
- **`dim_TradingFloors`** — the two trading floors (București primary HQ, Cluj-Napoca secondary).
- **`dim_Teams`** — the six teams (three per floor).
- **`dim_Employees`** — the 32 employees (24 traders, 6 team leads, 2 floor managers), with a self-FK for reporting line.
- **`dim_Accounts`** — trading accounts owned by traders (one or more per trader, EUR-denominated by default).
- **`dim_Markets`** — instruments traded across asset classes (equities, FX, crypto, commodities).
- **`dim_Sessions`** — trading sessions (pre-market, regular, after-hours) with Europe/Bucharest start/end times.
- **`dim_OrderType`** — static enumeration of order types (market, limit, stop, stop-limit).
- **`dim_Date`** — calendar dimension covering 2024-01-01 through 2030-12-31, with weekday/holiday flags.
- **`dim_UserRoles`** — AAD-principal-to-scope mapping that powers row-level security.
- **`fact_Trades`** — the only fact table, one row per closed-or-open trade.
- **`config_Capital`** — effective-dated capital baselines (global default + per-trader overrides).

### 2.2. Foreign-key relations

1. `dim_TradingFloors.company_id → dim_Companies.company_id`
2. `dim_Teams.floor_id → dim_TradingFloors.floor_id`
3. `dim_Employees.team_id → dim_Teams.team_id`
4. `dim_Employees.floor_id → dim_TradingFloors.floor_id` (denormalised for query convenience and RLS speed)
5. `dim_Employees.company_id → dim_Companies.company_id` (denormalised, single row)
6. `dim_Employees.manager_employee_id → dim_Employees.employee_id` (self-FK, nullable for the top of the chain)
7. `dim_Accounts.trader_id → dim_Employees.employee_id`
8. `fact_Trades.trader_id → dim_Employees.employee_id`
9. `fact_Trades.account_id → dim_Accounts.account_id`
10. `fact_Trades.market_id → dim_Markets.market_id`
11. `fact_Trades.session_id → dim_Sessions.session_id`
12. `fact_Trades.order_type_id → dim_OrderType.order_type_id`
13. `fact_Trades.trade_date_ro → dim_Date.calendar_date` (persisted computed column; see §4.1)
14. `config_Capital.trader_id → dim_Employees.employee_id` (nullable; NULL = global default)
15. `dim_UserRoles.employee_id → dim_Employees.employee_id` (nullable for admin / system roles)

The fact table is therefore reachable from every dimension via a single join — including the date dimension via the persisted `trade_date_ro` computed column — satisfying star-schema semantics for PowerBI's relationship engine.

---

## 3. Dimension tables

### 3.1. `dim_Companies`

**Purpose.** The legal-entity dimension. Only one row in production, but modelled properly so the schema generalises if the firm ever spins out subsidiaries.

| column_name        | data_type           | nullability | default                          | description                                  |
|--------------------|---------------------|-------------|----------------------------------|----------------------------------------------|
| `company_id`       | `INT IDENTITY(1,1)` | NOT NULL    | —                                | Surrogate key.                               |
| `legal_name`       | `NVARCHAR(200)`     | NOT NULL    | —                                | Legal name (e.g., "TCP Capital Management SRL"). |
| `short_name`       | `NVARCHAR(50)`      | NOT NULL    | —                                | Display name (e.g., "TCP").                  |
| `country_code`     | `CHAR(2)`           | NOT NULL    | `'RO'`                           | ISO-3166-1 alpha-2.                          |
| `base_currency`    | `CHAR(3)`           | NOT NULL    | `'EUR'`                          | ISO-4217 reporting currency.                 |
| `created_at`       | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()`            | Row creation time.                           |
| `updated_at`       | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()`            | Row last modification time.                  |

**Keys & constraints.**
- `PK_dim_Companies` on `company_id`.
- `UQ_dim_Companies_legal_name` unique on `legal_name`.
- `CHECK (LEN(country_code) = 2)`, `CHECK (LEN(base_currency) = 3)`.

**Slowly-changing strategy.** SCD1 (overwrite). The legal name almost never changes; if it does, the previous value is not needed for analytical history.

**Seed data.**

```sql
INSERT INTO dbo.dim_Companies (legal_name, short_name, country_code, base_currency)
VALUES (N'TCP Capital Management SRL', N'TCP', 'RO', 'EUR');
```

---

### 3.2. `dim_TradingFloors`

**Purpose.** The two physical trading floors of the firm. Used for floor-level aggregation and RLS scoping.

| column_name      | data_type           | nullability | default               | description                              |
|------------------|---------------------|-------------|-----------------------|------------------------------------------|
| `floor_id`       | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                           |
| `company_id`     | `INT`               | NOT NULL    | —                     | FK to `dim_Companies`.                   |
| `city`           | `NVARCHAR(100)`     | NOT NULL    | —                     | City (e.g., "București").                |
| `floor_code`     | `VARCHAR(10)`       | NOT NULL    | —                     | Short code (e.g., "BUC", "CLJ").         |
| `is_primary_hq`  | `BIT`               | NOT NULL    | `0`                   | 1 = primary HQ (one row only).           |
| `opened_on`      | `DATE`              | NULL        | —                     | First operational date.                  |
| `created_at`     | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                       |
| `updated_at`     | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row last modification time.              |

**Keys & constraints.**
- `PK_dim_TradingFloors` on `floor_id`.
- `FK_dim_TradingFloors_dim_Companies_company_id`.
- `UQ_dim_TradingFloors_floor_code` unique on `floor_code`.
- Filtered unique index `UX_dim_TradingFloors_PrimaryHQ` on `is_primary_hq WHERE is_primary_hq = 1` to enforce a single primary HQ.

**Slowly-changing strategy.** SCD1. Geographical reorganisations are out of scope.

**Seed data.**

```sql
INSERT INTO dbo.dim_TradingFloors (company_id, city, floor_code, is_primary_hq, opened_on)
VALUES (1, N'București',    'BUC', 1, '2018-01-01'),
       (1, N'Cluj-Napoca',  'CLJ', 0, '2021-06-01');
```

---

### 3.3. `dim_Teams`

**Purpose.** The six teams, three per floor. Drives team-level aggregation and the Team Lead RLS scope.

| column_name   | data_type           | nullability | default               | description                          |
|---------------|---------------------|-------------|-----------------------|--------------------------------------|
| `team_id`     | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                       |
| `floor_id`    | `INT`               | NOT NULL    | —                     | FK to `dim_TradingFloors`.           |
| `team_name`   | `NVARCHAR(100)`     | NOT NULL    | —                     | Team display name (e.g., "Alpha").   |
| `team_code`   | `VARCHAR(20)`       | NOT NULL    | —                     | Short code (e.g., "BUC-A").          |
| `created_at`  | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                   |
| `updated_at`  | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row last modification time.          |

**Keys & constraints.**
- `PK_dim_Teams` on `team_id`.
- `FK_dim_Teams_dim_TradingFloors_floor_id`.
- `UQ_dim_Teams_team_code` unique on `team_code`.

**Slowly-changing strategy.** SCD1. Renames overwrite; historical reports re-resolve via FK.

**Seed data.**

```sql
INSERT INTO dbo.dim_Teams (floor_id, team_name, team_code) VALUES
 (1, N'Alpha',    'BUC-A'),
 (1, N'Bravo',    'BUC-B'),
 (1, N'Charlie',  'BUC-C'),
 (2, N'Delta',    'CLJ-D'),
 (2, N'Echo',     'CLJ-E'),
 (2, N'Foxtrot',  'CLJ-F');
```

---

### 3.4. `dim_Employees`

**Purpose.** All 32 employees. Carries role, hierarchy, and a self-FK to encode the reporting line. Names are generated with Faker `ro_RO`; email follows `first.last@tcp-capital.ro`.

| column_name             | data_type           | nullability | default               | description                                      |
|-------------------------|---------------------|-------------|-----------------------|--------------------------------------------------|
| `employee_id`           | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                                   |
| `company_id`            | `INT`               | NOT NULL    | —                     | FK to `dim_Companies` (denormalised).            |
| `floor_id`              | `INT`               | NOT NULL    | —                     | FK to `dim_TradingFloors`.                       |
| `team_id`               | `INT`               | NOT NULL    | —                     | FK to `dim_Teams`.                               |
| `manager_employee_id`   | `INT`               | NULL        | —                     | Self-FK; NULL for the company-top role.          |
| `first_name`            | `NVARCHAR(80)`      | NOT NULL    | —                     | Given name.                                      |
| `last_name`             | `NVARCHAR(80)`      | NOT NULL    | —                     | Family name.                                     |
| `email`                 | `NVARCHAR(254)`     | NOT NULL    | —                     | Corporate email (`@tcp-capital.ro`).             |
| `employee_role`         | `VARCHAR(20)`       | NOT NULL    | —                     | `'trader' \| 'team_lead' \| 'floor_manager'`. Renamed from `[role]` to avoid the SQL keyword collision. |
| `hire_date`             | `DATE`              | NOT NULL    | —                     | Date of hire.                                    |
| `is_active`             | `BIT`               | NOT NULL    | `1`                   | 0 once an employee leaves.                       |
| `aad_object_id`         | `UNIQUEIDENTIFIER`  | NULL        | —                     | Azure AD object ID for AAD-based RLS lookup.     |
| `created_at`            | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                               |
| `updated_at`            | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row last modification time.                      |

**Keys & constraints.**
- `PK_dim_Employees` on `employee_id`.
- `FK_dim_Employees_dim_Companies_company_id`.
- `FK_dim_Employees_dim_TradingFloors_floor_id`.
- `FK_dim_Employees_dim_Teams_team_id`.
- `FK_dim_Employees_dim_Employees_manager_employee_id` (self-FK, `ON DELETE NO ACTION`).
- `UQ_dim_Employees_email` unique on `email`.
- `UQ_dim_Employees_aad_object_id` unique on `aad_object_id` (filtered `WHERE aad_object_id IS NOT NULL`).
- `CHECK (employee_role IN ('trader', 'team_lead', 'floor_manager'))`.
- `CHECK (email LIKE '%@tcp-capital.ro' AND email NOT LIKE '%@%@%')` — refuses double-`@` injection like `evil@nottcp.com@tcp-capital.ro`.
- Index `IX_dim_Employees_team_id_role` on `(team_id, employee_role)`.
- Index `IX_dim_Employees_floor_id_role` on `(floor_id, employee_role)`.

**No PII beyond first/last name, email, role, hierarchy FKs, hire_date.** No national IDs, addresses, salaries.

**Slowly-changing strategy.** SCD1 with an `is_active` soft-delete. Re-orgs (team change) overwrite the FK; historical fact rows still reference the employee, not their then-team.

**Seed data.** Generated at bootstrap time by the Python `tcp.synth` package using Faker `ro_RO` (24 traders + 6 team leads + 2 floor managers). Concrete names are inserted by the migration's post-deploy script; this design does not hardcode names.

---

### 3.5. `dim_Accounts`

**Purpose.** Trading accounts owned by traders. The 1:N relation lets a trader operate multiple sub-accounts (e.g., paper vs. live), each independently tracked.

| column_name      | data_type           | nullability | default               | description                                |
|------------------|---------------------|-------------|-----------------------|--------------------------------------------|
| `account_id`     | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                             |
| `trader_id`      | `INT`               | NOT NULL    | —                     | FK to `dim_Employees`.                     |
| `account_code`   | `VARCHAR(30)`       | NOT NULL    | —                     | External account identifier.               |
| `account_type`   | `VARCHAR(20)`       | NOT NULL    | `'live'`              | `'live' \| 'paper'`.                       |
| `currency`       | `CHAR(3)`           | NOT NULL    | `'EUR'`               | ISO-4217 account currency.                 |
| `opened_on`      | `DATE`              | NOT NULL    | —                     | Account open date.                         |
| `is_active`      | `BIT`               | NOT NULL    | `1`                   | 0 once closed.                             |
| `created_at`     | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                         |
| `updated_at`     | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row last modification time.                |

**Keys & constraints.**
- `PK_dim_Accounts` on `account_id`.
- `FK_dim_Accounts_dim_Employees_trader_id`.
- `UQ_dim_Accounts_account_code` unique on `account_code`.
- `CHECK (account_type IN ('live', 'paper'))`.
- `CHECK (LEN(currency) = 3)`.
- Index `IX_dim_Accounts_trader_id` on `(trader_id)`.

**Slowly-changing strategy.** SCD1. Account migrations between traders are rare; if needed, soft-close (`is_active = 0`) and open a new account.

**Seed note.** `opened_on` is required at insert time. The Python generator computes `opened_on = hire_date` of the owning trader (rounded to the calendar date) so accounts are never claimed to exist before the trader joined.

---

### 3.6. `dim_Markets`

**Purpose.** Instruments traded. Tight catalog of ~30 symbols spanning equities, FX, crypto, and commodities to keep synthetic data realistic without inflating the dimension.

| column_name     | data_type           | nullability | default               | description                                                |
|-----------------|---------------------|-------------|-----------------------|------------------------------------------------------------|
| `market_id`     | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                                             |
| `symbol`        | `VARCHAR(20)`       | NOT NULL    | —                     | Ticker (e.g., `AAPL`, `EURUSD`, `BTCUSD`, `XAUUSD`).       |
| `display_name`  | `NVARCHAR(100)`     | NOT NULL    | —                     | Human-readable name.                                       |
| `asset_class`   | `VARCHAR(20)`       | NOT NULL    | —                     | `'equity' \| 'fx' \| 'crypto' \| 'commodity'`.             |
| `quote_currency`| `CHAR(3)`           | NOT NULL    | —                     | Quote currency of the symbol (ISO-4217).                   |
| `tick_size`     | `DECIMAL(18,8)`     | NOT NULL    | —                     | Minimum price increment.                                   |
| `is_active`     | `BIT`               | NOT NULL    | `1`                   | 0 = delisted/disabled.                                     |
| `created_at`    | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                                         |
| `updated_at`    | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row last modification time.                                |

**Keys & constraints.**
- `PK_dim_Markets` on `market_id`.
- `UQ_dim_Markets_symbol` unique on `symbol`.
- `CHECK (asset_class IN ('equity','fx','crypto','commodity'))`.
- `CHECK (tick_size > 0)`.
- `CHECK (LEN(quote_currency) = 3)`.
- Index `IX_dim_Markets_asset_class` on `(asset_class, is_active)`.

**Slowly-changing strategy.** SCD1 with `is_active`. New listings = new rows.

**Seed data.** Approximately 30 rows covering: 10 US equities (`AAPL`, `MSFT`, `GOOGL`, `AMZN`, `META`, `TSLA`, `NVDA`, `JPM`, `XOM`, `SPY`), 8 FX pairs (`EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `AUDUSD`, `EURGBP`, `EURJPY`, `USDRON`), 6 cryptos (`BTCUSD`, `ETHUSD`, `SOLUSD`, `ADAUSD`, `XRPUSD`, `DOGEUSD`), 6 commodities (`XAUUSD`, `XAGUSD`, `WTI`, `BRENT`, `NATGAS`, `COPPER`). Loaded in the post-deploy script.

**Multi-currency note.** Instrument quote currencies vary across the seed (USD/JPY/CHF/GBP/EUR); the synthetic generator (`tcp/synth/fx_rates.py`, owned by Etapa 3) maintains a deterministic per-date FX-rate table and computes `gross_pnl_eur` / `net_pnl_eur` at trade close. The conversion is not run-time-feed-driven and the rates are reproducible from `(symbol, trade_date)`.

---

### 3.7. `dim_Sessions`

**Purpose.** Trading sessions, with start/end times normalised to Europe/Bucharest. Used to label each trade as pre-market / regular / after-hours.

| column_name         | data_type           | nullability | default               | description                                                 |
|---------------------|---------------------|-------------|-----------------------|-------------------------------------------------------------|
| `session_id`        | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                                              |
| `session_code`      | `VARCHAR(20)`       | NOT NULL    | —                     | `'pre_market' \| 'regular' \| 'after_hours'`.               |
| `display_name`      | `NVARCHAR(50)`      | NOT NULL    | —                     | Human-readable label.                                       |
| `start_time_local`  | `TIME(0)`           | NOT NULL    | —                     | Start in Europe/Bucharest local time.                       |
| `end_time_local`    | `TIME(0)`           | NOT NULL    | —                     | End in Europe/Bucharest local time.                         |
| `created_at`        | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                                          |

**Keys & constraints.**
- `PK_dim_Sessions` on `session_id`.
- `UQ_dim_Sessions_session_code` unique on `session_code`.
- `CHECK (start_time_local < end_time_local)`.
- `CHECK (session_code IN ('pre_market','regular','after_hours'))`.

**Slowly-changing strategy.** Static enum (effectively SCD1, but rows are immutable in practice).

**Seed data.**

```sql
INSERT INTO dbo.dim_Sessions (session_code, display_name, start_time_local, end_time_local) VALUES
 ('pre_market',  N'Pre-market',  '07:00', '09:30'),
 ('regular',     N'Regular',     '09:30', '17:30'),
 ('after_hours', N'After-hours', '17:30', '22:00');
```

---

### 3.8. `dim_OrderType`

**Purpose.** Static enumeration of order types. Locked at four values.

| column_name        | data_type           | nullability | default               | description                                   |
|--------------------|---------------------|-------------|-----------------------|-----------------------------------------------|
| `order_type_id`    | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                                |
| `order_type_code`  | `VARCHAR(20)`       | NOT NULL    | —                     | `'market' \| 'limit' \| 'stop' \| 'stop_limit'`. |
| `display_name`     | `NVARCHAR(50)`      | NOT NULL    | —                     | Human-readable label.                         |
| `is_directional`   | `BIT`               | NOT NULL    | `1`                   | 1 if the order takes a directional side (buy/sell). All v1.0 order types are directional. |
| `created_at`       | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                            |

**Keys & constraints.**
- `PK_dim_OrderType` on `order_type_id`.
- `UQ_dim_OrderType_order_type_code` unique on `order_type_code`.
- `CHECK (order_type_code IN ('market','limit','stop','stop_limit'))`.

**Slowly-changing strategy.** Static.

**Seed data.**

```sql
INSERT INTO dbo.dim_OrderType (order_type_code, display_name, is_directional) VALUES
 ('market',     N'Market',     1),
 ('limit',      N'Limit',      1),
 ('stop',       N'Stop',       1),
 ('stop_limit', N'Stop-limit', 1);
```

---

### 3.9. `dim_Date`

**Purpose.** Calendar dimension from 2024-01-01 through 2030-12-31 inclusive (2 558 rows). Provides weekday/holiday flags so PowerBI never has to compute them at query time.

| column_name       | data_type      | nullability | default | description                                              |
|-------------------|----------------|-------------|---------|----------------------------------------------------------|
| `date_id`         | `INT`          | NOT NULL    | —       | `yyyymmdd` numeric form (PK).                            |
| `calendar_date`   | `DATE`         | NOT NULL    | —       | The date itself.                                         |
| `iso_year`        | `INT`          | NOT NULL    | —       | ISO year.                                                |
| `iso_week`        | `INT`          | NOT NULL    | —       | ISO week 1–53.                                           |
| `year`            | `INT`          | NOT NULL    | —       | Gregorian year.                                          |
| `quarter`         | `TINYINT`      | NOT NULL    | —       | 1–4.                                                     |
| `month`           | `TINYINT`      | NOT NULL    | —       | 1–12.                                                    |
| `month_name_ro`   | `NVARCHAR(20)` | NOT NULL    | —       | Romanian month name (e.g., "Ianuarie").                  |
| `month_name_en`   | `NVARCHAR(20)` | NOT NULL    | —       | English month name (e.g., "January").                    |
| `day_of_month`    | `TINYINT`      | NOT NULL    | —       | 1–31.                                                    |
| `day_of_week`     | `TINYINT`      | NOT NULL    | —       | 1=Monday … 7=Sunday (ISO).                               |
| `is_weekday`      | `BIT`          | NOT NULL    | —       | 1 if Mon–Fri.                                            |
| `is_ro_holiday`   | `BIT`          | NOT NULL    | —       | 1 if a Romanian public holiday.                          |
| `ro_holiday_name` | `NVARCHAR(80)` | NULL        | —       | Holiday name (Romanian official designation) when `is_ro_holiday = 1`. |
| `en_holiday_name` | `NVARCHAR(80)` | NULL        | —       | Holiday name (English translation) when `is_ro_holiday = 1`.           |
| `fiscal_year`     | `INT`          | NOT NULL    | —       | Equal to `year` (Romania = calendar fiscal year). Persisted computed column with CHECK `fiscal_year = [year]`. |

**Keys & constraints.**
- `PK_dim_Date` on `date_id`.
- `UQ_dim_Date_calendar_date` unique on `calendar_date`.
- `CHECK (day_of_week BETWEEN 1 AND 7)`, `CHECK (quarter BETWEEN 1 AND 4)`, `CHECK (month BETWEEN 1 AND 12)`.
- Index `IX_dim_Date_calendar_date_is_weekday` on `(calendar_date, is_weekday, is_ro_holiday)` (covering for the holiday/trading-day lookups).

**Slowly-changing strategy.** Static, generated once by the post-deploy script using a numeric tally CTE; holidays are loaded from a hand-maintained list of Romanian public holidays 2024–2030 (New Year, Orthodox Easter Monday, Labour Day, Pentecost, Saint Mary, Romania National Day, Christmas, etc.).

---

### 3.10. `dim_UserRoles`

**Purpose.** Maps AAD principals (by `aad_object_id`) to their RLS scope. Lets the AI assistant identify *what slice* of `fact_Trades` the authenticated user is allowed to read.

| column_name     | data_type           | nullability | default               | description                                                   |
|-----------------|---------------------|-------------|-----------------------|---------------------------------------------------------------|
| `user_role_id`  | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                                                |
| `aad_object_id` | `UNIQUEIDENTIFIER`  | NOT NULL    | —                     | AAD object ID of the principal.                               |
| `employee_id`   | `INT`               | NULL        | —                     | FK to `dim_Employees` (NULL for service principals/admins).   |
| `scope`         | `VARCHAR(20)`       | NOT NULL    | —                     | `'trader' \| 'team_lead' \| 'floor_manager' \| 'admin'`.      |
| `is_active`     | `BIT`               | NOT NULL    | `1`                   | 0 to revoke.                                                  |
| `created_at`    | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                                            |
| `updated_at`    | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row last modification time.                                   |

**Keys & constraints.**
- `PK_dim_UserRoles` on `user_role_id`.
- `FK_dim_UserRoles_dim_Employees_employee_id`.
- Filtered unique index `UX_dim_UserRoles_aad_object_id_active` on `aad_object_id WHERE is_active = 1` — allows soft-revoke + re-onboard without losing audit history.
- Covering index `IX_dim_UserRoles_aad_object_id_INC` on `(aad_object_id, is_active) INCLUDE (employee_id, scope)` — supports the one-shot scope resolution inside `rls.fn_TradesPredicate` (see §9.2).
- `CHECK (scope IN ('trader','team_lead','floor_manager','admin'))`.
- `CHECK ((scope = 'admin') OR (employee_id IS NOT NULL))`.

**Slowly-changing strategy.** SCD1 with `is_active` soft-delete.

---

## 4. Fact tables

### 4.1. `fact_Trades`

**Grain.** One row per trade. A trade may be `is_open = 1` (entry recorded, exit pending) or closed (exit recorded, PnL realised). The natural key is `trade_uid`.

| column_name        | data_type           | nullability | default               | description                                                            |
|--------------------|---------------------|-------------|-----------------------|------------------------------------------------------------------------|
| `trade_uid`        | `VARCHAR(14)`       | NOT NULL    | —                     | Natural key, format `T<YYYYMMDD>-<NNNN>`.                              |
| `trader_id`        | `INT`               | NOT NULL    | —                     | FK to `dim_Employees`.                                                 |
| `account_id`       | `INT`               | NOT NULL    | —                     | FK to `dim_Accounts`.                                                  |
| `market_id`        | `INT`               | NOT NULL    | —                     | FK to `dim_Markets`.                                                   |
| `session_id`       | `INT`               | NOT NULL    | —                     | FK to `dim_Sessions`.                                                  |
| `order_type_id`    | `INT`               | NOT NULL    | —                     | FK to `dim_OrderType`.                                                 |
| `side`             | `CHAR(1)`           | NOT NULL    | —                     | `'B'` = buy / long, `'S'` = sell / short.                              |
| `quantity`         | `DECIMAL(18,4)`     | NOT NULL    | —                     | Units traded.                                                          |
| `price_entry`      | `DECIMAL(18,6)`     | NOT NULL    | —                     | Entry price in the symbol's quote currency.                            |
| `price_exit`       | `DECIMAL(18,6)`     | NULL        | —                     | Exit price (NULL while open).                                          |
| `time_entry`       | `DATETIMEOFFSET(3)` | NOT NULL    | —                     | Entry timestamp, Europe/Bucharest offset.                              |
| `time_exit`        | `DATETIMEOFFSET(3)` | NULL        | —                     | Exit timestamp (NULL while open).                                      |
| `gross_pnl_eur`    | `DECIMAL(18,4)`     | NULL        | —                     | Stored. Computed by the generator at exit time, then frozen.           |
| `commission_eur`   | `DECIMAL(18,4)`     | NOT NULL    | `0`                   | Commission expressed in EUR.                                           |
| `net_pnl_eur`      | `DECIMAL(18,4)`     | NULL        | —                     | `gross_pnl_eur - commission_eur`. NULL while open.                     |
| `fx_rate_to_eur`   | `DECIMAL(18,8)`     | NULL        | —                     | FX rate (quote currency → EUR) used at trade close; NULL for open trades and EUR-quoted instruments. Auditability for non-EUR PnL conversion. |
| `is_open`          | `BIT`               | NOT NULL    | `0`                   | 1 while trade has no exit.                                             |
| `trade_date_ro`    | `AS CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE) PERSISTED` | NOT NULL | — | Persisted computed column; canonical Bucharest-local trade date. FK to `dim_Date.calendar_date`. |
| `created_at`       | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                                                     |
| `updated_at`       | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row last modification time.                                            |

**Note on PnL field naming.** Both `gross_pnl_eur` (before commissions/fees) and `net_pnl_eur` (after commissions) are persisted. KPI formulas reference `gross_pnl_eur` explicitly; legacy references to `pnl_eur` should be read as `gross_pnl_eur`. The `gross_pnl_eur` and `net_pnl_eur` columns are write-once after a trade transitions to `is_open = 0`; the generator MUST treat them as immutable post-close (defence-in-depth could add an `AFTER UPDATE` trigger, deferred to v1.1).

**Why `gross_pnl_eur` and `net_pnl_eur` are stored (not computed columns).** Two reasons: (i) the EUR conversion for non-EUR instruments depends on the FX rate **at the moment the trade closed**, which is not derivable from the row alone, so the generator computes it once and persists it; (ii) storing avoids recomputation on every analytical scan, materially helping the AI-assistant query budget.

**Keys & constraints.**
- `PK_fact_Trades` clustered on `time_entry, trade_uid` — see "Indexes" below for the rationale.
- `UQ_fact_Trades_trade_uid` unique nonclustered on `trade_uid` (since clustered is on `time_entry`).
- FKs: `trader_id`, `account_id`, `market_id`, `session_id`, `order_type_id`, `trade_date_ro → dim_Date(calendar_date)` (enables PowerBI star-schema relationships against the date dimension).
- `CK_fact_Trades_trade_uid_format` — `CHECK (trade_uid LIKE 'T[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]')` — enforces the `T<YYYYMMDD>-<NNNN>` shape.
- `CK_fact_Trades_trade_uid_date_valid` — `CHECK (TRY_CONVERT(DATE, SUBSTRING(trade_uid, 2, 8), 112) IS NOT NULL)` — refuses syntactically valid but logically invalid dates such as `T20260230-0001`.
- `CHECK (side IN ('B','S'))`.
- `CHECK (quantity > 0)`.
- `CHECK (price_entry > 0)`.
- `CHECK (price_exit IS NULL OR price_exit > 0)`.
- `CHECK (commission_eur >= 0)`.
- `CK_fact_Trades_open_closed` — `CHECK ((is_open = 1 AND time_exit IS NULL AND price_exit IS NULL AND gross_pnl_eur IS NULL AND net_pnl_eur IS NULL) OR (is_open = 0 AND time_exit IS NOT NULL AND price_exit IS NOT NULL AND gross_pnl_eur IS NOT NULL AND net_pnl_eur IS NOT NULL))` — couples the open/closed state to all four nullable columns.
- `CHECK (time_exit IS NULL OR time_exit >= time_entry)`.
- **Application-layer invariant (not a CHECK constraint)**: when a trade closes (`is_open = 0`) against a non-EUR-quoted instrument (`dim_Markets.quote_currency != 'EUR'`), `fx_rate_to_eur` MUST be non-NULL for auditability of the EUR conversion. SQL Server CHECK constraints cannot reference other tables, so this invariant is enforced at the application boundary by the synthetic generator (Etapa 3) and validated in Etapa 8 by a CI assertion `tests/sql/test_fx_rate_completeness.sql` that runs `SELECT COUNT(*) FROM fact_Trades f JOIN dim_Markets m ON m.market_id = f.market_id WHERE f.is_open = 0 AND m.quote_currency != 'EUR' AND f.fx_rate_to_eur IS NULL` and expects zero rows.

**Indexes.**
- **Clustered**: `PK_fact_Trades` on `(time_entry, trade_uid)`. Justification: nearly every analytical query is time-ranged (`WHERE time_entry BETWEEN @from AND @to`), and inserts are monotonic in `time_entry`, so a clustered index here minimises page splits and gives range scans optimal locality. `trade_uid` breaks ties and guarantees uniqueness on the cluster key.
- `IX_fact_Trades_trader_id_time_entry` on `(trader_id, time_entry) INCLUDE (market_id, side, net_pnl_eur, quantity, price_entry, commission_eur)`. Covers the AI-assistant "give me trader X's last N days" query.
- `IX_fact_Trades_market_id_time_entry` on `(market_id, time_entry) INCLUDE (trader_id, net_pnl_eur)`. Covers symbol-level leaderboards.
- `IX_fact_Trades_is_open` filtered on `(trader_id, time_entry) WHERE is_open = 1`. Tiny index for the "open positions" panel.
- `IX_fact_Trades_trade_date_ro` on `(trade_date_ro)`. Supports the FK to `dim_Date(calendar_date)` and PowerBI date-dimension joins.
- Columnstore acceleration (a future `NCCI_fact_Trades_Analytics`) is deferred to a later migration (`V003+`) once row counts justify it. v1.0 ships with row store only.

**Partitioning.** At an expected steady-state of 24 traders × ~10 trades/day × ~250 trading days/year ≈ **60 000 rows/year**, the table will not exceed ~360 000 rows over the six-year reporting window. Azure SQL Free comfortably handles tables an order of magnitude larger without partitioning, and partitioning adds operational cost (partition functions/schemes, sliding-window maintenance) that is not justified here. **Decision: no partitioning.** Re-evaluate if the firm scales past 200 traders.

---

## 5. Config tables

### 5.1. `config_Capital`

**Purpose.** Effective-dated capital baseline. The default is **80 000 EUR**, applied globally (`trader_id IS NULL`); per-trader overrides are inserted with a non-NULL `trader_id`. The effective-from/effective-to semantics let analytical views resolve "the active baseline for trader X at moment T" deterministically.

| column_name      | data_type           | nullability | default               | description                                                  |
|------------------|---------------------|-------------|-----------------------|--------------------------------------------------------------|
| `capital_id`     | `INT IDENTITY(1,1)` | NOT NULL    | —                     | Surrogate key.                                               |
| `trader_id`      | `INT`               | NULL        | —                     | FK to `dim_Employees`. NULL = global default.                |
| `amount_eur`     | `DECIMAL(18,2)`     | NOT NULL    | —                     | Capital baseline in EUR.                                     |
| `effective_from` | `DATETIMEOFFSET(3)` | NOT NULL    | —                     | Start of validity, inclusive.                                |
| `effective_to`   | `DATETIMEOFFSET(3)` | NULL        | —                     | End of validity, exclusive. NULL = open-ended (current row). |
| `note`           | `NVARCHAR(400)`     | NULL        | —                     | Free-text rationale (audit trail).                           |
| `created_at`     | `DATETIMEOFFSET(3)` | NOT NULL    | `SYSDATETIMEOFFSET()` | Row creation time.                                           |

**Keys & constraints.**
- `PK_config_Capital` on `capital_id`.
- `FK_config_Capital_dim_Employees_trader_id` (nullable).
- `CHECK (amount_eur > 0)`.
- `CHECK (effective_to IS NULL OR effective_to > effective_from)`.
- Filtered unique index `UX_config_Capital_global_current` on `(trader_id, effective_from)` where `trader_id IS NULL` — at most one global row may share a start instant.
- Filtered unique index `UX_config_Capital_trader_current` on `(trader_id, effective_from)` where `trader_id IS NOT NULL` — prevents two per-trader rows from sharing the same `effective_from`, which would render `fn_GetCapitalBaseline` non-deterministic.
- Index `IX_config_Capital_trader_id_effective_from` on `(trader_id, effective_from DESC) INCLUDE (amount_eur, effective_to)`.

**Seed data.**

```sql
INSERT INTO dbo.config_Capital (trader_id, amount_eur, effective_from, effective_to, note)
VALUES (NULL, 80000.00, '2024-01-01T00:00:00+02:00', NULL,
        N'Initial global baseline.');
```

**Look-up pattern.** Resolve the active baseline for `(@trader_id, @as_of)` by taking the per-trader override if any, otherwise the global row. The scalar UDF defined in §8 encapsulates this:

```sql
-- Equivalent inline query:
SELECT TOP 1 amount_eur
FROM dbo.config_Capital
WHERE (trader_id = @trader_id OR trader_id IS NULL)
  AND effective_from <= @as_of
  AND (effective_to IS NULL OR effective_to > @as_of)
ORDER BY CASE WHEN trader_id = @trader_id THEN 0 ELSE 1 END,
         effective_from DESC;
```

The `ORDER BY` clause makes the per-trader override always win over the global default at the same instant.

---

## 6. Views

All views expose `trade_date_ro = CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE)` as the canonical date slicer.

### 6.1. `v_trades_enriched`

**Grain.** One row per trade in `fact_Trades`, with all dimensions joined and derived measures inlined.

**Columns.** `trade_uid`, `trade_date_ro`, `time_entry`, `time_exit`, `trader_id`, `trader_full_name`, `team_id`, `team_name`, `floor_id`, `floor_city`, `market_id`, `symbol`, `asset_class`, `session_code`, `order_type_code`, `side`, `quantity`, `price_entry`, `price_exit`, `gross_pnl_eur`, `commission_eur`, `net_pnl_eur`, `is_open`, `holding_time_minutes`, `pnl_per_unit`.

`return_pct` is intentionally not exposed at the trade row level because mixing EUR-converted PnL with quote-currency notional yields meaningless ratios for non-EUR instruments; the period-level `tvf_RiskMetrics` (§8.4) computes returns against the capital baseline instead.

```sql
CREATE OR ALTER VIEW dbo.v_trades_enriched
WITH SCHEMABINDING
AS
SELECT
    f.trade_uid,
    f.trade_date_ro,
    f.time_entry,
    f.time_exit,
    f.trader_id,
    e.first_name + N' ' + e.last_name                                 AS trader_full_name,
    e.team_id,
    t.team_name,
    e.floor_id,
    tf.city                                                            AS floor_city,
    f.market_id,
    m.symbol,
    m.asset_class,
    s.session_code,
    o.order_type_code,
    f.side,
    f.quantity,
    f.price_entry,
    f.price_exit,
    f.gross_pnl_eur,
    f.commission_eur,
    f.net_pnl_eur,
    f.is_open,
    CASE
        WHEN f.time_exit IS NULL THEN NULL
        ELSE DATEDIFF(MINUTE, f.time_entry, f.time_exit)
    END                                                                AS holding_time_minutes,
    CASE
        WHEN f.quantity = 0 OR f.net_pnl_eur IS NULL THEN NULL
        ELSE f.net_pnl_eur / f.quantity
    END                                                                AS pnl_per_unit
FROM dbo.fact_Trades            AS f
JOIN dbo.dim_Employees          AS e  ON e.employee_id  = f.trader_id
JOIN dbo.dim_Teams              AS t  ON t.team_id      = e.team_id
JOIN dbo.dim_TradingFloors      AS tf ON tf.floor_id    = e.floor_id
JOIN dbo.dim_Markets            AS m  ON m.market_id    = f.market_id
JOIN dbo.dim_Sessions           AS s  ON s.session_id   = f.session_id
JOIN dbo.dim_OrderType          AS o  ON o.order_type_id = f.order_type_id;
```

> **Schema-binding note.** All five `v_*` views use `WITH SCHEMABINDING` for refactor safety (column-rename guards). `AT TIME ZONE` is non-deterministic, so the views cannot be **indexed** views; `trade_date_ro` is therefore materialised as a persisted computed column on `fact_Trades` (§4.1) rather than recomputed inside the view.

### 6.2. `v_employee_performance`

**Grain.** One row per `(trade_date_ro, employee_id)`.

**Columns.** `trade_date_ro`, `employee_id`, `trader_full_name`, `team_id`, `floor_id`, `trade_count`, `win_count`, `loss_count`, `win_rate`, `gross_pnl_eur_total`, `commission_eur_total`, `net_pnl_eur_total`, `avg_holding_time_minutes`.

```sql
CREATE OR ALTER VIEW dbo.v_employee_performance
WITH SCHEMABINDING
AS
SELECT
    v.trade_date_ro,
    v.trader_id                                                       AS employee_id,
    MAX(v.trader_full_name)                                            AS trader_full_name,
    MAX(v.team_id)                                                     AS team_id,
    MAX(v.floor_id)                                                    AS floor_id,
    COUNT_BIG(*)                                                       AS trade_count,
    SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1 ELSE 0 END)                 AS win_count,
    SUM(CASE WHEN v.net_pnl_eur < 0 THEN 1 ELSE 0 END)                 AS loss_count,
    CAST(SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1.0 ELSE 0 END)
         / NULLIF(COUNT_BIG(*), 0) AS DECIMAL(9,6))                    AS win_rate,
    SUM(v.gross_pnl_eur)                                               AS gross_pnl_eur_total,
    SUM(v.commission_eur)                                              AS commission_eur_total,
    SUM(v.net_pnl_eur)                                                 AS net_pnl_eur_total,
    AVG(CAST(v.holding_time_minutes AS DECIMAL(18,4)))                 AS avg_holding_time_minutes
FROM dbo.v_trades_enriched AS v
WHERE v.is_open = 0
GROUP BY v.trade_date_ro, v.trader_id;
```

### 6.3. `v_team_performance`

**Grain.** One row per `(trade_date_ro, team_id)`.

```sql
CREATE OR ALTER VIEW dbo.v_team_performance
WITH SCHEMABINDING
AS
SELECT
    v.trade_date_ro,
    v.team_id,
    MAX(v.team_name)                                                   AS team_name,
    MAX(v.floor_id)                                                    AS floor_id,
    COUNT_BIG(*)                                                       AS trade_count,
    SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1 ELSE 0 END)                 AS win_count,
    SUM(CASE WHEN v.net_pnl_eur < 0 THEN 1 ELSE 0 END)                 AS loss_count,
    CAST(SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1.0 ELSE 0 END)
         / NULLIF(COUNT_BIG(*), 0) AS DECIMAL(9,6))                    AS win_rate,
    SUM(v.gross_pnl_eur)                                               AS gross_pnl_eur_total,
    SUM(v.commission_eur)                                              AS commission_eur_total,
    SUM(v.net_pnl_eur)                                                 AS net_pnl_eur_total,
    AVG(CAST(v.holding_time_minutes AS DECIMAL(18,4)))                 AS avg_holding_time_minutes
FROM dbo.v_trades_enriched AS v
WHERE v.is_open = 0
GROUP BY v.trade_date_ro, v.team_id;
```

### 6.4. `v_floor_performance`

**Grain.** One row per `(trade_date_ro, floor_id)`.

```sql
CREATE OR ALTER VIEW dbo.v_floor_performance
WITH SCHEMABINDING
AS
SELECT
    v.trade_date_ro,
    v.floor_id,
    MAX(v.floor_city)                                                  AS floor_city,
    COUNT_BIG(*)                                                       AS trade_count,
    SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1 ELSE 0 END)                 AS win_count,
    SUM(CASE WHEN v.net_pnl_eur < 0 THEN 1 ELSE 0 END)                 AS loss_count,
    CAST(SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1.0 ELSE 0 END)
         / NULLIF(COUNT_BIG(*), 0) AS DECIMAL(9,6))                    AS win_rate,
    SUM(v.gross_pnl_eur)                                               AS gross_pnl_eur_total,
    SUM(v.commission_eur)                                              AS commission_eur_total,
    SUM(v.net_pnl_eur)                                                 AS net_pnl_eur_total,
    AVG(CAST(v.holding_time_minutes AS DECIMAL(18,4)))                 AS avg_holding_time_minutes
FROM dbo.v_trades_enriched AS v
WHERE v.is_open = 0
GROUP BY v.trade_date_ro, v.floor_id;
```

### 6.5. `v_daily_pnl`

**Grain.** One row per `(employee_id, trade_date_ro)`, with cumulative PnL via a window function.

```sql
CREATE OR ALTER VIEW dbo.v_daily_pnl
WITH SCHEMABINDING
AS
SELECT
    ep.employee_id,
    ep.trader_full_name,
    ep.team_id,
    ep.floor_id,
    ep.trade_date_ro,
    ep.trade_count,
    ep.net_pnl_eur_total,
    SUM(ep.net_pnl_eur_total) OVER (
        PARTITION BY ep.employee_id
        ORDER BY ep.trade_date_ro
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )                                                                  AS cumulative_net_pnl_eur
FROM dbo.v_employee_performance AS ep;
```

> **Gap behaviour.** `v_daily_pnl` produces one row per `(employee_id, trade_date_ro)` only on days the trader actually closed at least one trade. There is no implicit calendar gap-fill: drawdown / cumulative-equity visuals that need a daily series across non-trading days must left-join `dim_Date` and forward-fill with `LAG(..., 1, ...) IGNORE NULLS` (deferred to v1.1 as `v_employee_daily_equity` if dashboard need arises).
>
> **Related artifact.** `tvf_RiskMetrics` (§8.4) consumes this view and exposes Sharpe / Sortino / VaR-95 / downside-stdev / total PnL per `(employee_id, period)`.

---

## 7. Stored procedures

### 7.1. `usp_GenerateDailyTrades`

**Contract.** Called by the Function App's Timer Trigger at 07:00 RO weekdays. Inserts a synthetic trading day for the **target trade date** passed in. **Idempotent**: if rows already exist for that `trade_date_ro`, the proc returns gracefully with `rows_inserted = 0, status = 'already_generated'` (no error raised). Transactional: a failure mid-batch leaves the database untouched. Returns the number of rows inserted and a status string.

**SESSION_CONTEXT precondition.** The caller MUST have set `aad_object_id` via `sp_set_session_context` before invoking this proc (see §9). For the daily generator path this is the Function App MI's known AAD object id, registered in `dim_UserRoles` with `scope='admin'` so that the RLS block predicate permits the bulk INSERT.

```sql
CREATE OR ALTER PROCEDURE dbo.usp_GenerateDailyTrades
    @trade_date DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @trade_date IS NULL
        THROW 50001, 'usp_GenerateDailyTrades: @trade_date is required.', 1;

    IF dbo.fn_IsTradingDay(@trade_date) = 0
        THROW 50002, 'usp_GenerateDailyTrades: @trade_date is not a trading day (weekend or RO holiday).', 1;

    BEGIN TRY
        BEGIN TRAN;

        IF EXISTS (
            SELECT 1
            FROM dbo.fact_Trades
            WHERE trade_date_ro = @trade_date
        )
        BEGIN
            IF @@TRANCOUNT > 0 ROLLBACK;
            SELECT 0 AS rows_inserted, 'already_generated' AS status;
            RETURN 0;
        END;

        -- The actual synthetic-trade generation is driven by the Python worker
        -- (tcp.synth.generate_day) which streams INSERTs through a single
        -- transaction using a TVP. This proc is the *contract* the worker calls;
        -- the worker prepares the dataset and issues a single
        --   INSERT INTO dbo.fact_Trades (...) SELECT ... FROM @trades_tvp;
        -- inside this transaction by way of a child proc or sp_executesql.

        DECLARE @inserted INT = @@ROWCOUNT;

        COMMIT;

        SELECT @inserted AS rows_inserted, 'inserted' AS status;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK;
        THROW;
    END CATCH
END;
```

> **Note.** The generator's INSERT body is provided by `tcp.synth.generate_day` in Etapa 3; this proc's responsibility is the transactional envelope, idempotency check, and trading-day guard. Concretising the TVP type (`tt_TradeBatch`) is part of Etapa 3 and will appear in `V002__synth_tvp.sql`.

### 7.2. `usp_GetEmployeePerformance`

**Contract.** Read-only proc used by the AI assistant. Returns per-day aggregates for one employee over a closed date range.

```sql
CREATE OR ALTER PROCEDURE dbo.usp_GetEmployeePerformance
    @employee_id INT,
    @from        DATE,
    @to          DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @employee_id IS NULL OR @from IS NULL OR @to IS NULL
        THROW 50010, 'usp_GetEmployeePerformance: all parameters are required.', 1;

    IF @from > @to
        THROW 50011, 'usp_GetEmployeePerformance: @from must be <= @to.', 1;

    BEGIN TRY
        SELECT
            ep.trade_date_ro,
            ep.employee_id,
            ep.trader_full_name,
            ep.team_id,
            ep.floor_id,
            ep.trade_count,
            ep.win_count,
            ep.loss_count,
            ep.win_rate,
            ep.gross_pnl_eur_total,
            ep.commission_eur_total,
            ep.net_pnl_eur_total,
            ep.avg_holding_time_minutes
        FROM dbo.v_employee_performance AS ep
        WHERE ep.employee_id    = @employee_id
          AND ep.trade_date_ro >= @from
          AND ep.trade_date_ro <= @to
        ORDER BY ep.trade_date_ro;
    END TRY
    BEGIN CATCH
        THROW;
    END CATCH
END;
```

### 7.3. `usp_GetTopPerformers`

**Contract.** Returns the top-N performers for the requested scope (`'trader' | 'team' | 'floor'`) over a closed date range, sorted by total net PnL.

```sql
CREATE OR ALTER PROCEDURE dbo.usp_GetTopPerformers
    @scope NVARCHAR(20),
    @from  DATE,
    @to    DATE,
    @top_n INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @scope NOT IN (N'trader', N'team', N'floor')
        THROW 50020, 'usp_GetTopPerformers: @scope must be one of trader/team/floor.', 1;

    IF @from IS NULL OR @to IS NULL OR @from > @to
        THROW 50021, 'usp_GetTopPerformers: invalid date range.', 1;

    IF @top_n IS NULL OR @top_n <= 0 OR @top_n > 100
        THROW 50022, 'usp_GetTopPerformers: @top_n must be between 1 and 100.', 1;

    BEGIN TRY
        IF @scope = N'trader'
        BEGIN
            SELECT TOP (@top_n)
                ep.employee_id                 AS entity_id,
                MAX(ep.trader_full_name)       AS entity_name,
                SUM(ep.net_pnl_eur_total)      AS net_pnl_eur_total,
                SUM(ep.trade_count)            AS trade_count,
                SUM(ep.win_count) * 1.0 / NULLIF(SUM(ep.trade_count), 0) AS win_rate
            FROM dbo.v_employee_performance AS ep
            WHERE ep.trade_date_ro BETWEEN @from AND @to
            GROUP BY ep.employee_id
            ORDER BY net_pnl_eur_total DESC;
        END
        ELSE IF @scope = N'team'
        BEGIN
            SELECT TOP (@top_n)
                tp.team_id                     AS entity_id,
                MAX(tp.team_name)              AS entity_name,
                SUM(tp.net_pnl_eur_total)      AS net_pnl_eur_total,
                SUM(tp.trade_count)            AS trade_count,
                SUM(tp.win_count) * 1.0 / NULLIF(SUM(tp.trade_count), 0) AS win_rate
            FROM dbo.v_team_performance AS tp
            WHERE tp.trade_date_ro BETWEEN @from AND @to
            GROUP BY tp.team_id
            ORDER BY net_pnl_eur_total DESC;
        END
        ELSE
        BEGIN
            SELECT TOP (@top_n)
                fp.floor_id                    AS entity_id,
                MAX(fp.floor_city)             AS entity_name,
                SUM(fp.net_pnl_eur_total)      AS net_pnl_eur_total,
                SUM(fp.trade_count)            AS trade_count,
                SUM(fp.win_count) * 1.0 / NULLIF(SUM(fp.trade_count), 0) AS win_rate
            FROM dbo.v_floor_performance AS fp
            WHERE fp.trade_date_ro BETWEEN @from AND @to
            GROUP BY fp.floor_id
            ORDER BY net_pnl_eur_total DESC;
        END
    END TRY
    BEGIN CATCH
        THROW;
    END CATCH
END;
```

---

## 8. Functions

### 8.1. `fn_GetCapitalBaseline` (scalar)

```sql
CREATE OR ALTER FUNCTION dbo.fn_GetCapitalBaseline
(
    @trader_id INT,
    @as_of     DATETIMEOFFSET(3)
)
RETURNS DECIMAL(18,2)
WITH SCHEMABINDING
AS
BEGIN
    DECLARE @amount DECIMAL(18,2);

    SELECT TOP 1 @amount = c.amount_eur
    FROM dbo.config_Capital AS c
    WHERE (c.trader_id = @trader_id OR c.trader_id IS NULL)
      AND c.effective_from <= @as_of
      AND (c.effective_to IS NULL OR c.effective_to > @as_of)
    ORDER BY CASE WHEN c.trader_id = @trader_id THEN 0 ELSE 1 END,
             c.effective_from DESC;

    RETURN @amount;
END;
```

### 8.2. `fn_IsTradingDay` (scalar)

Returns `1` if the given date is a Monday–Friday Romanian non-holiday, else `0`. Relies on the pre-populated `dim_Date`.

```sql
CREATE OR ALTER FUNCTION dbo.fn_IsTradingDay
(
    @d DATE
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    DECLARE @result BIT = 0;

    SELECT @result =
        CASE
            WHEN d.is_weekday = 1 AND d.is_ro_holiday = 0 THEN 1
            ELSE 0
        END
    FROM dbo.dim_Date AS d
    WHERE d.calendar_date = @d;

    RETURN ISNULL(@result, 0);
END;
```

### 8.3. `fn_PreviousBusinessDay` (scalar)

Returns the most recent trading day strictly earlier than `@d`. Used by the cron generator: on Monday it returns the previous Friday; otherwise the previous calendar day (skipping holidays).

```sql
CREATE OR ALTER FUNCTION dbo.fn_PreviousBusinessDay
(
    @d DATE
)
RETURNS DATE
WITH SCHEMABINDING
AS
BEGIN
    DECLARE @prev DATE;

    SELECT TOP 1 @prev = d.calendar_date
    FROM dbo.dim_Date AS d
    WHERE d.calendar_date < @d
      AND d.is_weekday    = 1
      AND d.is_ro_holiday = 0
    ORDER BY d.calendar_date DESC;

    RETURN @prev;
END;
```

### 8.4. `tvf_RiskMetrics` (inline TVF)

Inline table-valued function exposing risk metrics — mean / stdev / downside-stdev / VaR-95 / total PnL — for an employee over a closed date range. Backs the Sharpe, Sortino, VaR-95 and drawdown KPIs in `01_BR §4.4`.

```sql
CREATE OR ALTER FUNCTION dbo.tvf_RiskMetrics
(
    @employee_id INT,
    @from        DATE,
    @to          DATE
)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT
        COUNT_BIG(*)                                                                AS trading_days,
        AVG(ep.net_pnl_eur_total)                                                   AS mean_daily_pnl,
        STDEV(ep.net_pnl_eur_total)                                                 AS stdev_daily_pnl,
        STDEV(CASE WHEN ep.net_pnl_eur_total < 0 THEN ep.net_pnl_eur_total END)     AS stdev_downside,
        PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY ep.net_pnl_eur_total) OVER ()  AS var_95,
        SUM(ep.net_pnl_eur_total)                                                   AS total_net_pnl
    FROM dbo.v_employee_performance AS ep
    WHERE ep.employee_id    = @employee_id
      AND ep.trade_date_ro BETWEEN @from AND @to;
```

### 8.5. `tvf_GetCapitalBaseline` (inline TVF — MJ-02 follow-up)

Inline-TVF alternative to the scalar `fn_GetCapitalBaseline`. Use via `OUTER APPLY` in analytical queries so the optimizer can inline the lookup instead of falling into row-by-row scalar UDF evaluation.

```sql
CREATE OR ALTER FUNCTION dbo.tvf_GetCapitalBaseline
(
    @trader_id INT,
    @as_of     DATETIMEOFFSET(3)
)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT TOP 1 c.amount_eur
    FROM dbo.config_Capital AS c
    WHERE (c.trader_id = @trader_id OR c.trader_id IS NULL)
      AND c.effective_from <= @as_of
      AND (c.effective_to IS NULL OR c.effective_to > @as_of)
    ORDER BY CASE WHEN c.trader_id = @trader_id THEN 0 ELSE 1 END,
             c.effective_from DESC;
```

---

## 9. Row-level security

### 9.1. Scope semantics

| `dim_UserRoles.scope` | What the principal can read in `fact_Trades`                                 |
|-----------------------|-------------------------------------------------------------------------------|
| `'trader'`            | Only rows where `trader_id` matches the principal's own `employee_id`.        |
| `'team_lead'`         | All rows where the trader belongs to the team lead's team.                    |
| `'floor_manager'`     | All rows where the trader belongs to the floor manager's floor.               |
| `'admin'`             | All rows (no filter).                                                         |

Principals are identified by the AAD object ID returned by `USER_NAME()` / `SUSER_SNAME()` when the connection uses an AAD token. Service identities (the Function App's managed identity) are mapped via a dedicated `dim_UserRoles` row with `scope = 'admin'`.

**Connection contract.** Every Function App connection MUST call `EXEC sp_set_session_context @key=N'aad_object_id', @value=@oid, @read_only=1` before issuing any query against the views. If unset, the predicate returns 0 rows (deny-by-default). For the daily generator path, the MI's own AAD object id must be present in `dim_UserRoles` with `scope='admin'`; this row is inserted by the Etapa 4 post-provision script. Connection-pool workers MUST also call `sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0` on checkout to clear any sticky value from a previous principal; failing to do so is the #1 cause of cross-tenant RLS leaks.

### 9.2. Predicate function

The predicate function resolves the calling principal's `scope` once via a CROSS APPLY, then evaluates the row's trader_id against the resolved scope. The two roles `dim_Employees` plays — **principal row** (`p`, the calling user) and **trader row of the fact row** (`t`, the trade's owner) — are aliased distinctly for readability.

`tcp_ai_assistant` requires explicit `SELECT` on `dim_UserRoles` and `dim_Employees` because the predicate is evaluated as part of the query plan over `fact_Trades`, and ownership chaining does not bridge predicate evaluation (see §10.2 grants).

```sql
IF SCHEMA_ID('rls') IS NULL
    EXEC('CREATE SCHEMA rls AUTHORIZATION dbo');
GO

CREATE OR ALTER FUNCTION rls.fn_TradesPredicate(@trader_id_in_row INT)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS result
    FROM (
        -- p = principal row (the calling user resolved from SESSION_CONTEXT)
        SELECT TOP 1
            ur.scope,
            ur.employee_id      AS principal_employee_id,
            e.team_id           AS principal_team,
            e.floor_id          AS principal_floor
        FROM dbo.dim_UserRoles AS ur
        LEFT JOIN dbo.dim_Employees AS e
            ON e.employee_id = ur.employee_id
        WHERE ur.is_active = 1
          AND ur.aad_object_id = CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)
    ) AS p
    WHERE
           p.scope = 'admin'
        OR (p.scope = 'trader' AND p.principal_employee_id = @trader_id_in_row)
        OR EXISTS (
            -- t = trader row of the fact row
            SELECT 1 FROM dbo.dim_Employees AS t
            WHERE t.employee_id = @trader_id_in_row
              AND (
                   (p.scope = 'team_lead'     AND t.team_id  = p.principal_team)
                OR (p.scope = 'floor_manager' AND t.floor_id = p.principal_floor)
              )
        );
GO
```

**Deny-by-default.** `SESSION_CONTEXT(N'aad_object_id')` returns NULL when the connection has not set it; the CAST returns NULL; the WHERE clause `ur.aad_object_id = NULL` is never true; the inner derived table `p` yields no rows; the predicate returns no rows — i.e., zero data. This is the intended posture and it is documented for the operators so empty result sets on the assistant are not silently misdiagnosed.

The Function App, before issuing any user-bound query, sets the session context once per connection:

```sql
EXEC sp_set_session_context @key = N'aad_object_id', @value = @aad_object_id, @read_only = 1;
```

### 9.3. Security policy

```sql
CREATE SECURITY POLICY rls.TradesAccessPolicy
ADD FILTER PREDICATE rls.fn_TradesPredicate(trader_id) ON dbo.fact_Trades,
ADD BLOCK  PREDICATE rls.fn_TradesPredicate(trader_id) ON dbo.fact_Trades AFTER INSERT,
ADD BLOCK  PREDICATE rls.fn_TradesPredicate(trader_id) ON dbo.fact_Trades AFTER UPDATE
WITH (STATE = ON);
```

The block predicates prevent a Trader's session from inserting/updating rows attributed to another trader (defence in depth). The generator role is exempt by virtue of holding `scope = 'admin'` in `dim_UserRoles`.

**Note on AFTER DELETE (intentional omission).** The BLOCK PREDICATE today covers only `AFTER INSERT` and `AFTER UPDATE`. `AFTER DELETE` is intentionally omitted because no role currently has `DELETE` rights on `dbo.fact_Trades` or `dbo.fact_DailyTraderPnL` (FILTER PREDICATE additionally restricts DELETE to visible rows). A future migration that adds DELETE rights to any role MUST re-evaluate this decision and extend the policy with `ADD BLOCK PREDICATE rls.fn_TradesPredicate(trader_id) ... AFTER DELETE` on both fact tables. See `review_etapa2_security_pass1.md` MN-02.

---

## 10. Roles & permissions

### 10.1. Bootstrap

The very first schema apply runs as the **SQL admin login** that Bicep provisions for the Azure SQL server (`@@SERVERNAME` admin user, password retrieved from Key Vault, used only once at deploy time). The deployment script then immediately:

1. Creates a contained AAD admin via `CREATE USER [TCP DB Admins] FROM EXTERNAL PROVIDER; ALTER ROLE db_owner ADD MEMBER [TCP DB Admins];`.
2. Drops the SQL admin's `CONNECT` privilege on the user database (the server admin still exists at the server level, but cannot reach the data tier).
3. **AAD-only auth flip (post-deploy).** The post-provision hook (`infra/scripts/postprovision.ps1`) calls `Set-AzSqlServerActiveDirectoryOnlyAuthentication -ServerName ... -Enable $true` against the logical server, then deletes the `SQL-ADMIN-PASSWORD-BOOTSTRAP` Key Vault secret. CI verifies the secret is absent before marking the deploy green. This is tied to the Etapa 4 acceptance checklist.
4. From this point on, all connections — interactive admins, the Function App, and PowerBI — authenticate via AAD only.

### 10.2. Custom database roles

The platform defines four roles with an unambiguous two-role split between the assistant and the generator. The same Function App managed identity holds both `tcp_ai_assistant` and `tcp_generator`; the application chooses the per-request role by selecting which connection string / session scope to use.

- **`tcp_ai_assistant`** — SELECT on views only, EXECUTE on read-only stored procs; **no** direct fact/dim access by way of a `db_datareader` membership. The path used by the AI assistant (`/api/ask`).
- **`tcp_generator`** — INSERT/UPDATE on `fact_Trades` only, SELECT on dimensions plus EXECUTE on generator support functions. The path used by the Timer Trigger.
- **`tcp_bi_reader`** — SELECT on the five `v_*` views and the dimension tables PowerBI's relationship engine needs. The path used by the PowerBI Service principal (or personal AAD account, see §16).
- **`tcp_admin`** — full read/write/EXECUTE; thesis-context decision (acceptable for a single-developer project; documented as a least-privilege gap that production would split into narrower roles).

```sql
-- AI assistant: read-only, never directly touches the fact table.
CREATE ROLE tcp_ai_assistant AUTHORIZATION dbo;
GRANT EXECUTE ON dbo.usp_GetEmployeePerformance TO tcp_ai_assistant;
GRANT EXECUTE ON dbo.usp_GetTopPerformers       TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_trades_enriched          TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_employee_performance     TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_team_performance         TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_floor_performance        TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_daily_pnl                TO tcp_ai_assistant;
-- RLS predicate evaluation requires direct SELECT on these two tables
-- (ownership chaining does not bridge predicate evaluation).
GRANT SELECT  ON dbo.dim_UserRoles              TO tcp_ai_assistant;
GRANT SELECT  ON dbo.dim_Employees              TO tcp_ai_assistant;
-- No SELECT on dbo.fact_Trades directly; the views are the API.

-- Daily generator: writes the day's trades and nothing else.
CREATE ROLE tcp_generator AUTHORIZATION dbo;
GRANT EXECUTE ON dbo.usp_GenerateDailyTrades   TO tcp_generator;
GRANT INSERT, UPDATE ON dbo.fact_Trades        TO tcp_generator;
GRANT SELECT ON dbo.dim_Employees              TO tcp_generator;
GRANT SELECT ON dbo.dim_Accounts               TO tcp_generator;
GRANT SELECT ON dbo.dim_Markets                TO tcp_generator;
GRANT SELECT ON dbo.dim_Sessions               TO tcp_generator;
GRANT SELECT ON dbo.dim_OrderType              TO tcp_generator;
GRANT SELECT ON dbo.dim_Date                   TO tcp_generator;
GRANT SELECT ON dbo.dim_UserRoles              TO tcp_generator;   -- for RLS predicate
GRANT SELECT ON dbo.config_Capital             TO tcp_generator;
GRANT EXECUTE ON dbo.fn_GetCapitalBaseline     TO tcp_generator;
GRANT EXECUTE ON dbo.fn_IsTradingDay           TO tcp_generator;
GRANT EXECUTE ON dbo.fn_PreviousBusinessDay    TO tcp_generator;

-- PowerBI Service principal: SELECT on the analytical surface + dimension tables
-- (PowerBI's relationship engine needs real tables to build the semantic model).
CREATE ROLE tcp_bi_reader AUTHORIZATION dbo;
GRANT SELECT ON dbo.v_trades_enriched      TO tcp_bi_reader;
GRANT SELECT ON dbo.v_employee_performance TO tcp_bi_reader;
GRANT SELECT ON dbo.v_team_performance     TO tcp_bi_reader;
GRANT SELECT ON dbo.v_floor_performance    TO tcp_bi_reader;
GRANT SELECT ON dbo.v_daily_pnl            TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Employees          TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Teams              TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_TradingFloors      TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Markets            TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Sessions           TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_OrderType          TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Date               TO tcp_bi_reader;

-- Full admin.
CREATE ROLE tcp_admin AUTHORIZATION dbo;
ALTER ROLE db_datareader ADD MEMBER tcp_admin;
ALTER ROLE db_datawriter ADD MEMBER tcp_admin;
GRANT EXECUTE ON SCHEMA::dbo TO tcp_admin;
```

> **Grants persist across `CREATE OR ALTER`** but are **lost on `DROP`/`CREATE`**. Always use `CREATE OR ALTER` for procs that hold grants (e.g., `usp_GenerateDailyTrades` whose body is replaced in `V002__synth_tvp.sql`).

The Function App's managed identity is added to `tcp_ai_assistant` (for `/api/ask`) **and** `tcp_generator` (for the Timer Trigger). PowerBI Service's AAD service principal is added to `tcp_bi_reader` (and is registered as an `admin`-scope row in `dim_UserRoles` so the RLS predicate does not strip its result set).

---

## 11. Migration strategy

**Decision: Flyway-style numbered scripts** under `db/migrations/V001__init.sql`, `V002__synth_tvp.sql`, etc.

**Rationale.** DACPAC + `sqlpackage` is powerful but optimised for declarative model-vs-DB diffing, which is heavyweight for a thesis project; Flyway-style scripts give git-grade reviewability, trivial CI integration (a single `sqlcmd` call per file in lexical order), and a transparent change log for the thesis appendix. Each script is **forward-only**; rollback scripts live in `db/migrations/rollback/V001__init.down.sql` and are applied manually if a deploy must be reverted.

**Conventions.**
- File pattern: `V<NNN>__<snake_case_description>.sql`. Versions are dense and never reused.
- Each file begins with `SET XACT_ABORT ON; BEGIN TRAN;` and ends with `COMMIT;` so a failure leaves no half-applied schema.
- A `dbo.schema_history` table (created in `V001`) records each applied version, its checksum, and the apply timestamp; the CI runner refuses to re-apply a file whose checksum changed.
- Pre-deploy hooks (e.g., disabling RLS policy before a bulk reload) live in numbered files; post-deploy seeding (dim tables, `dim_Date` populate) also lives in numbered files. There is no implicit pre/post split — order is determined by the filename only.

---

## 12. Backups & disaster recovery

- **Azure SQL Free tier — included:** point-in-time restore over the last **7 days** at 5-minute granularity. **RPO ≈ 5 minutes, RTO ≈ 12 hours** worst-case (depends on Azure portal restore queue).
- **Long-term retention:** the Free tier does not include LTR or geo-redundant backups. We therefore export a **weekly BACPAC** to Azure Blob Storage (cold tier) via a **second Function App Timer Trigger** (NCRONTAB `0 0 8 * * 0`, Sunday 08:00 RO) using the Function MI for storage RBAC. Cost is well within the Blob Storage free tier (5 GB). See `03_arch §11` for the runbook and the MI grant on the `bacpac-exports` container.
- **Recovery rehearsal:** the bachelor's thesis includes a documented rehearsal in `docs/runbooks/restore.md` (created in Etapa 4).
- **Data loss tolerance for this project:** because the data is fully synthetic and re-generatable by `tcp.synth`, an actual restore is rarely required; the BACPAC export exists primarily as evidence of a working DR posture.

---

## 13. Performance budget

| Query                                             | Cold p95 | Warm p95 | Justification                                                                                                                                                                                                       |
|---------------------------------------------------|----------|----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Last-30-day floor leaderboard (PowerBI / `usp_GetTopPerformers @scope='floor'`) | ≤ 200 ms | ≤ 50 ms  | 30 days × ~250 trades/day = ~7 500 rows. Index seek on the clustered key range `time_entry`, grouped aggregate over `floor_id`. Expected plan: `Clustered Index Seek → Stream Aggregate`.                              |
| AI-assistant single-question query (any `usp_GetEmployeePerformance` call) | ≤ 800 ms | ≤ 200 ms | View `v_employee_performance` aggregates 30 days × ~10 trades = ~300 rows for one trader. Plan: `IX_fact_Trades_trader_id_time_entry Seek → Nested Loops to dims → Hash Aggregate`. 800 ms cold accounts for auto-resume from pause. |
| Daily generator (`usp_GenerateDailyTrades`, ~250 rows inserted) | ≤ 5 s    | ≤ 5 s    | 250 single-row inserts via TVP in one transaction. With the clustered index monotonic on `time_entry` and FK lookups served from buffer pool, throughput is bound by network RTT, not by the engine.                |

The dominant risk factor is **auto-resume latency** after a 60-minute idle period (typical Azure SQL Serverless cold-start: 30–60 s for the first connection). The PowerBI Scheduled Refresh at 07:30 RO and the Timer Trigger at 07:00 RO are designed so that the first connection of the day pays this cost; subsequent user-driven queries hit a warm instance.

**Cold vs warm layering.** The figures above are **warm-path** budgets, i.e., they assume the DB is not auto-paused. The cold-path (post-pause) auto-resume adds +30–60 s for the very first query of the day. The assistant end-to-end p95 (Python init + Anthropic call + SQL query) is owned by `03_arch §14`; this document budgets **only** the SQL leg. With `trade_date_ro` now persisted on `fact_Trades` (§4.1), PowerBI's star-schema relationship to `dim_Date` becomes a real index seek rather than a runtime time-zone CAST, lifting the warm budget for date-sliced visuals.

---

## 14. Naming-convention enforcement

CI runs the following script against the deployed DB and fails the build if it returns any rows. The same script is also embedded in `tests/sql/test_naming_convention.sql`.

```sql
-- Tables: prefix_PascalName
SELECT
    TABLE_SCHEMA, TABLE_NAME,
    N'Non-compliant table name (expected ^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$).' AS violation
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_TYPE  = 'BASE TABLE'
  AND TABLE_SCHEMA NOT IN ('sys','INFORMATION_SCHEMA')
  AND TABLE_NAME NOT IN ('schema_history')          -- migration metadata exempted
  AND (
        TABLE_NAME NOT LIKE 'fact[_]%'
    AND TABLE_NAME NOT LIKE 'dim[_]%'
    AND TABLE_NAME NOT LIKE 'config[_]%'
  );

-- Tables: PascalCase after the prefix, no underscores, no leading lowercase letter.
SELECT
    TABLE_SCHEMA, TABLE_NAME,
    N'PascalCase violation after the prefix.' AS violation
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_TYPE  = 'BASE TABLE'
  AND TABLE_NAME NOT IN ('schema_history')
  AND (
        TABLE_NAME LIKE 'fact[_]%'
    OR  TABLE_NAME LIKE 'dim[_]%'
    OR  TABLE_NAME LIKE 'config[_]%'
  )
  AND PATINDEX('%[_]%',
        SUBSTRING(TABLE_NAME, CHARINDEX('_', TABLE_NAME) + 1, 256)
      ) > 0;                                         -- no further underscores allowed

SELECT
    TABLE_SCHEMA, TABLE_NAME,
    N'First character after the prefix must be A-Z.' AS violation
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_TYPE = 'BASE TABLE'
  AND TABLE_NAME NOT IN ('schema_history')
  AND (
        TABLE_NAME LIKE 'fact[_]%'
    OR  TABLE_NAME LIKE 'dim[_]%'
    OR  TABLE_NAME LIKE 'config[_]%'
  )
  AND SUBSTRING(TABLE_NAME, CHARINDEX('_', TABLE_NAME) + 1, 1) NOT LIKE '[A-Z]';

-- Views: v_snake_case
SELECT
    TABLE_SCHEMA, TABLE_NAME,
    N'Views must follow v_snake_case.' AS violation
FROM INFORMATION_SCHEMA.VIEWS
WHERE TABLE_NAME NOT LIKE 'v[_]%'
   OR TABLE_NAME LIKE '%[A-Z]%';
```

The CI gate runs in the `db-naming` job; non-zero output marks the workflow as failed.

---

## 15. DDL bundle — `db/migrations/V001__init.sql`

The single, re-runnable initialisation script. It guards every object with `OBJECT_ID(...) IS NULL` (or the schema/role/policy equivalent) so a re-apply on a fresh DB is a clean idempotent operation, and so a partial failure can be replayed.

```sql
/******************************************************************************
 * TCP — Trading Central Panel
 * V001__init.sql
 *
 * Initial schema for Azure SQL Database — Free Offer.
 * Author: TODO
 * Created: 2026-05-15
 *
 * Idempotent: every object is created only if missing. Re-running on a
 * fresh DB is safe; re-running on a partially-applied DB resumes from
 * the first missing object.
 ******************************************************************************/

SET XACT_ABORT ON;
SET NOCOUNT    ON;
BEGIN TRAN;

----------------------------------------------------------------------------
-- 0. Schema history (migration ledger)
----------------------------------------------------------------------------
-- schema_history column shape was updated 2026-05-15 to file-name-keyed form after Etapa-2 review (see review_etapa2_sql_pass1.md MJ-02).
IF OBJECT_ID(N'dbo.schema_history', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.schema_history
    (
        script_name      NVARCHAR(200)     NOT NULL CONSTRAINT PK_schema_history PRIMARY KEY,
        applied_at_utc   DATETIME2(3)      NOT NULL,
        checksum         NVARCHAR(128)     NULL
    );
END;

----------------------------------------------------------------------------
-- 1. Schemas
----------------------------------------------------------------------------
IF SCHEMA_ID(N'rls') IS NULL
    EXEC(N'CREATE SCHEMA rls AUTHORIZATION dbo;');

----------------------------------------------------------------------------
-- 2. Dimension tables
----------------------------------------------------------------------------

-- dim_Companies
IF OBJECT_ID(N'dbo.dim_Companies', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_Companies
    (
        company_id     INT IDENTITY(1,1) NOT NULL,
        legal_name     NVARCHAR(200)     NOT NULL,
        short_name     NVARCHAR(50)      NOT NULL,
        country_code   CHAR(2)           NOT NULL CONSTRAINT DF_dim_Companies_country_code  DEFAULT ('RO'),
        base_currency  CHAR(3)           NOT NULL CONSTRAINT DF_dim_Companies_base_currency DEFAULT ('EUR'),
        created_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Companies_created_at    DEFAULT (SYSDATETIMEOFFSET()),
        updated_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Companies_updated_at    DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_Companies                PRIMARY KEY (company_id),
        CONSTRAINT UQ_dim_Companies_legal_name     UNIQUE      (legal_name),
        CONSTRAINT CK_dim_Companies_country_code   CHECK (LEN(country_code)  = 2),
        CONSTRAINT CK_dim_Companies_base_currency  CHECK (LEN(base_currency) = 3)
    );
END;

-- dim_TradingFloors
IF OBJECT_ID(N'dbo.dim_TradingFloors', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_TradingFloors
    (
        floor_id       INT IDENTITY(1,1) NOT NULL,
        company_id     INT               NOT NULL,
        city           NVARCHAR(100)     NOT NULL,
        floor_code     VARCHAR(10)       NOT NULL,
        is_primary_hq  BIT               NOT NULL CONSTRAINT DF_dim_TradingFloors_is_primary_hq DEFAULT (0),
        opened_on      DATE              NULL,
        created_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_TradingFloors_created_at    DEFAULT (SYSDATETIMEOFFSET()),
        updated_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_TradingFloors_updated_at    DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_TradingFloors                       PRIMARY KEY (floor_id),
        CONSTRAINT UQ_dim_TradingFloors_floor_code            UNIQUE      (floor_code),
        CONSTRAINT FK_dim_TradingFloors_dim_Companies_company_id
            FOREIGN KEY (company_id) REFERENCES dbo.dim_Companies(company_id)
    );

    CREATE UNIQUE INDEX UX_dim_TradingFloors_PrimaryHQ
        ON dbo.dim_TradingFloors(is_primary_hq)
        WHERE is_primary_hq = 1;
END;

-- dim_Teams
IF OBJECT_ID(N'dbo.dim_Teams', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_Teams
    (
        team_id     INT IDENTITY(1,1) NOT NULL,
        floor_id    INT               NOT NULL,
        team_name   NVARCHAR(100)     NOT NULL,
        team_code   VARCHAR(20)       NOT NULL,
        created_at  DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Teams_created_at DEFAULT (SYSDATETIMEOFFSET()),
        updated_at  DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Teams_updated_at DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_Teams                              PRIMARY KEY (team_id),
        CONSTRAINT UQ_dim_Teams_team_code                    UNIQUE      (team_code),
        CONSTRAINT FK_dim_Teams_dim_TradingFloors_floor_id
            FOREIGN KEY (floor_id) REFERENCES dbo.dim_TradingFloors(floor_id)
    );
END;

-- dim_Employees
IF OBJECT_ID(N'dbo.dim_Employees', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_Employees
    (
        employee_id          INT IDENTITY(1,1) NOT NULL,
        company_id           INT               NOT NULL,
        floor_id             INT               NOT NULL,
        team_id              INT               NOT NULL,
        manager_employee_id  INT               NULL,
        first_name           NVARCHAR(80)      NOT NULL,
        last_name            NVARCHAR(80)      NOT NULL,
        email                NVARCHAR(254)     NOT NULL,
        employee_role        VARCHAR(20)       NOT NULL,
        hire_date            DATE              NOT NULL,
        is_active            BIT               NOT NULL CONSTRAINT DF_dim_Employees_is_active   DEFAULT (1),
        aad_object_id        UNIQUEIDENTIFIER  NULL,
        created_at           DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Employees_created_at  DEFAULT (SYSDATETIMEOFFSET()),
        updated_at           DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Employees_updated_at  DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_Employees                                                PRIMARY KEY (employee_id),
        CONSTRAINT UQ_dim_Employees_email                                          UNIQUE      (email),
        CONSTRAINT FK_dim_Employees_dim_Companies_company_id
            FOREIGN KEY (company_id) REFERENCES dbo.dim_Companies(company_id),
        CONSTRAINT FK_dim_Employees_dim_TradingFloors_floor_id
            FOREIGN KEY (floor_id) REFERENCES dbo.dim_TradingFloors(floor_id),
        CONSTRAINT FK_dim_Employees_dim_Teams_team_id
            FOREIGN KEY (team_id) REFERENCES dbo.dim_Teams(team_id),
        CONSTRAINT FK_dim_Employees_dim_Employees_manager_employee_id
            FOREIGN KEY (manager_employee_id) REFERENCES dbo.dim_Employees(employee_id),
        CONSTRAINT CK_dim_Employees_role
            CHECK (employee_role IN ('trader','team_lead','floor_manager')),
        CONSTRAINT CK_dim_Employees_email_domain
            CHECK (email LIKE '%@tcp-capital.ro' AND email NOT LIKE '%@%@%')
    );

    CREATE UNIQUE INDEX UQ_dim_Employees_aad_object_id
        ON dbo.dim_Employees(aad_object_id)
        WHERE aad_object_id IS NOT NULL;

    CREATE INDEX IX_dim_Employees_team_id_role  ON dbo.dim_Employees(team_id,  employee_role);
    CREATE INDEX IX_dim_Employees_floor_id_role ON dbo.dim_Employees(floor_id, employee_role);
END;

-- dim_Accounts
IF OBJECT_ID(N'dbo.dim_Accounts', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_Accounts
    (
        account_id    INT IDENTITY(1,1) NOT NULL,
        trader_id     INT               NOT NULL,
        account_code  VARCHAR(30)       NOT NULL,
        account_type  VARCHAR(20)       NOT NULL CONSTRAINT DF_dim_Accounts_account_type DEFAULT ('live'),
        currency      CHAR(3)           NOT NULL CONSTRAINT DF_dim_Accounts_currency     DEFAULT ('EUR'),
        opened_on     DATE              NOT NULL,
        is_active     BIT               NOT NULL CONSTRAINT DF_dim_Accounts_is_active    DEFAULT (1),
        created_at    DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Accounts_created_at   DEFAULT (SYSDATETIMEOFFSET()),
        updated_at    DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Accounts_updated_at   DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_Accounts                                  PRIMARY KEY (account_id),
        CONSTRAINT UQ_dim_Accounts_account_code                     UNIQUE      (account_code),
        CONSTRAINT FK_dim_Accounts_dim_Employees_trader_id
            FOREIGN KEY (trader_id) REFERENCES dbo.dim_Employees(employee_id),
        CONSTRAINT CK_dim_Accounts_account_type CHECK (account_type IN ('live','paper')),
        CONSTRAINT CK_dim_Accounts_currency     CHECK (LEN(currency) = 3)
    );

    CREATE INDEX IX_dim_Accounts_trader_id ON dbo.dim_Accounts(trader_id);
END;

-- dim_Markets
IF OBJECT_ID(N'dbo.dim_Markets', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_Markets
    (
        market_id      INT IDENTITY(1,1) NOT NULL,
        symbol         VARCHAR(20)       NOT NULL,
        display_name   NVARCHAR(100)     NOT NULL,
        asset_class    VARCHAR(20)       NOT NULL,
        quote_currency CHAR(3)           NOT NULL,
        tick_size      DECIMAL(18,8)     NOT NULL,
        is_active      BIT               NOT NULL CONSTRAINT DF_dim_Markets_is_active  DEFAULT (1),
        created_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Markets_created_at DEFAULT (SYSDATETIMEOFFSET()),
        updated_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Markets_updated_at DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_Markets                       PRIMARY KEY (market_id),
        CONSTRAINT UQ_dim_Markets_symbol                UNIQUE      (symbol),
        CONSTRAINT CK_dim_Markets_asset_class
            CHECK (asset_class IN ('equity','fx','crypto','commodity')),
        CONSTRAINT CK_dim_Markets_tick_size      CHECK (tick_size > 0),
        CONSTRAINT CK_dim_Markets_quote_currency CHECK (LEN(quote_currency) = 3)
    );

    CREATE INDEX IX_dim_Markets_asset_class ON dbo.dim_Markets(asset_class, is_active);
END;

-- dim_Sessions
IF OBJECT_ID(N'dbo.dim_Sessions', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_Sessions
    (
        session_id        INT IDENTITY(1,1) NOT NULL,
        session_code      VARCHAR(20)       NOT NULL,
        display_name      NVARCHAR(50)      NOT NULL,
        start_time_local  TIME(0)           NOT NULL,
        end_time_local    TIME(0)           NOT NULL,
        created_at        DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_Sessions_created_at DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_Sessions                            PRIMARY KEY (session_id),
        CONSTRAINT UQ_dim_Sessions_session_code               UNIQUE      (session_code),
        CONSTRAINT CK_dim_Sessions_time_window                CHECK (start_time_local < end_time_local),
        CONSTRAINT CK_dim_Sessions_session_code
            CHECK (session_code IN ('pre_market','regular','after_hours'))
    );
END;

-- dim_OrderType
IF OBJECT_ID(N'dbo.dim_OrderType', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_OrderType
    (
        order_type_id    INT IDENTITY(1,1) NOT NULL,
        order_type_code  VARCHAR(20)       NOT NULL,
        display_name     NVARCHAR(50)      NOT NULL,
        is_directional   BIT               NOT NULL CONSTRAINT DF_dim_OrderType_is_directional DEFAULT (1),
        created_at       DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_OrderType_created_at     DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_OrderType                       PRIMARY KEY (order_type_id),
        CONSTRAINT UQ_dim_OrderType_order_type_code       UNIQUE      (order_type_code),
        CONSTRAINT CK_dim_OrderType_order_type_code
            CHECK (order_type_code IN ('market','limit','stop','stop_limit'))
    );
END;

-- dim_Date
IF OBJECT_ID(N'dbo.dim_Date', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_Date
    (
        date_id          INT             NOT NULL,
        calendar_date    DATE            NOT NULL,
        iso_year         INT             NOT NULL,
        iso_week         INT             NOT NULL,
        [year]           INT             NOT NULL,
        [quarter]        TINYINT         NOT NULL,
        [month]          TINYINT         NOT NULL,
        month_name_ro    NVARCHAR(20)    NOT NULL,
        month_name_en    NVARCHAR(20)    NOT NULL,
        day_of_month     TINYINT         NOT NULL,
        day_of_week      TINYINT         NOT NULL,
        is_weekday       BIT             NOT NULL,
        is_ro_holiday    BIT             NOT NULL,
        ro_holiday_name  NVARCHAR(80)    NULL,
        en_holiday_name  NVARCHAR(80)    NULL,
        fiscal_year      AS [year] PERSISTED,
        CONSTRAINT PK_dim_Date                              PRIMARY KEY (date_id),
        CONSTRAINT UQ_dim_Date_calendar_date                UNIQUE      (calendar_date),
        CONSTRAINT CK_dim_Date_day_of_week CHECK (day_of_week BETWEEN 1 AND 7),
        CONSTRAINT CK_dim_Date_quarter     CHECK ([quarter] BETWEEN 1 AND 4),
        CONSTRAINT CK_dim_Date_month       CHECK ([month]   BETWEEN 1 AND 12)
    );

    CREATE INDEX IX_dim_Date_calendar_date_is_weekday
        ON dbo.dim_Date(calendar_date, is_weekday, is_ro_holiday);
END;

-- dim_UserRoles
IF OBJECT_ID(N'dbo.dim_UserRoles', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.dim_UserRoles
    (
        user_role_id   INT IDENTITY(1,1) NOT NULL,
        aad_object_id  UNIQUEIDENTIFIER  NOT NULL,
        employee_id    INT               NULL,
        scope          VARCHAR(20)       NOT NULL,
        is_active      BIT               NOT NULL CONSTRAINT DF_dim_UserRoles_is_active  DEFAULT (1),
        created_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_UserRoles_created_at DEFAULT (SYSDATETIMEOFFSET()),
        updated_at     DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_dim_UserRoles_updated_at DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_dim_UserRoles                        PRIMARY KEY (user_role_id),
        CONSTRAINT FK_dim_UserRoles_dim_Employees_employee_id
            FOREIGN KEY (employee_id) REFERENCES dbo.dim_Employees(employee_id),
        CONSTRAINT CK_dim_UserRoles_scope
            CHECK (scope IN ('trader','team_lead','floor_manager','admin')),
        CONSTRAINT CK_dim_UserRoles_scope_employee
            CHECK ((scope = 'admin') OR (employee_id IS NOT NULL))
    );

    -- Filtered unique: only one ACTIVE row per AAD principal; soft-revoked rows
    -- remain for audit and re-onboarding does not violate uniqueness.
    CREATE UNIQUE INDEX UX_dim_UserRoles_aad_object_id_active
        ON dbo.dim_UserRoles(aad_object_id)
        WHERE is_active = 1;

    -- Covering index for the RLS predicate's one-shot principal resolution.
    CREATE INDEX IX_dim_UserRoles_aad_object_id_INC
        ON dbo.dim_UserRoles(aad_object_id, is_active)
        INCLUDE (employee_id, scope);
END;

----------------------------------------------------------------------------
-- 3. Config tables
----------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.config_Capital', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.config_Capital
    (
        capital_id      INT IDENTITY(1,1) NOT NULL,
        trader_id       INT               NULL,
        amount_eur      DECIMAL(18,2)     NOT NULL,
        effective_from  DATETIMEOFFSET(3) NOT NULL,
        effective_to    DATETIMEOFFSET(3) NULL,
        note            NVARCHAR(400)     NULL,
        created_at      DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_config_Capital_created_at DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_config_Capital                                   PRIMARY KEY (capital_id),
        CONSTRAINT FK_config_Capital_dim_Employees_trader_id
            FOREIGN KEY (trader_id) REFERENCES dbo.dim_Employees(employee_id),
        CONSTRAINT CK_config_Capital_amount_eur     CHECK (amount_eur > 0),
        CONSTRAINT CK_config_Capital_effective_window
            CHECK (effective_to IS NULL OR effective_to > effective_from)
    );

    CREATE UNIQUE INDEX UX_config_Capital_global_current
        ON dbo.config_Capital(trader_id, effective_from)
        WHERE trader_id IS NULL;

    CREATE UNIQUE INDEX UX_config_Capital_trader_current
        ON dbo.config_Capital(trader_id, effective_from)
        WHERE trader_id IS NOT NULL;

    CREATE INDEX IX_config_Capital_trader_id_effective_from
        ON dbo.config_Capital(trader_id, effective_from DESC)
        INCLUDE (amount_eur, effective_to);
END;

----------------------------------------------------------------------------
-- 4. Fact tables
----------------------------------------------------------------------------
IF OBJECT_ID(N'dbo.fact_Trades', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.fact_Trades
    (
        trade_uid       VARCHAR(14)       NOT NULL,
        trader_id       INT               NOT NULL,
        account_id      INT               NOT NULL,
        market_id       INT               NOT NULL,
        session_id      INT               NOT NULL,
        order_type_id   INT               NOT NULL,
        side            CHAR(1)           NOT NULL,
        quantity        DECIMAL(18,4)     NOT NULL,
        price_entry     DECIMAL(18,6)     NOT NULL,
        price_exit      DECIMAL(18,6)     NULL,
        time_entry      DATETIMEOFFSET(3) NOT NULL,
        time_exit       DATETIMEOFFSET(3) NULL,
        gross_pnl_eur   DECIMAL(18,4)     NULL,
        commission_eur  DECIMAL(18,4)     NOT NULL CONSTRAINT DF_fact_Trades_commission_eur DEFAULT (0),
        net_pnl_eur     DECIMAL(18,4)     NULL,
        fx_rate_to_eur  DECIMAL(18,8)     NULL,
        is_open         BIT               NOT NULL CONSTRAINT DF_fact_Trades_is_open     DEFAULT (0),
        trade_date_ro   AS CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE) PERSISTED NOT NULL,
        created_at      DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_fact_Trades_created_at  DEFAULT (SYSDATETIMEOFFSET()),
        updated_at      DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_fact_Trades_updated_at  DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_fact_Trades PRIMARY KEY CLUSTERED (time_entry, trade_uid),
        CONSTRAINT UQ_fact_Trades_trade_uid UNIQUE NONCLUSTERED (trade_uid),
        CONSTRAINT FK_fact_Trades_dim_Employees_trader_id
            FOREIGN KEY (trader_id)     REFERENCES dbo.dim_Employees(employee_id),
        CONSTRAINT FK_fact_Trades_dim_Accounts_account_id
            FOREIGN KEY (account_id)    REFERENCES dbo.dim_Accounts(account_id),
        CONSTRAINT FK_fact_Trades_dim_Markets_market_id
            FOREIGN KEY (market_id)     REFERENCES dbo.dim_Markets(market_id),
        CONSTRAINT FK_fact_Trades_dim_Sessions_session_id
            FOREIGN KEY (session_id)    REFERENCES dbo.dim_Sessions(session_id),
        CONSTRAINT FK_fact_Trades_dim_OrderType_order_type_id
            FOREIGN KEY (order_type_id) REFERENCES dbo.dim_OrderType(order_type_id),
        CONSTRAINT FK_fact_Trades_dim_Date
            FOREIGN KEY (trade_date_ro) REFERENCES dbo.dim_Date(calendar_date),
        CONSTRAINT CK_fact_Trades_trade_uid_format
            CHECK (trade_uid LIKE 'T[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
        CONSTRAINT CK_fact_Trades_trade_uid_date_valid
            CHECK (TRY_CONVERT(DATE, SUBSTRING(trade_uid, 2, 8), 112) IS NOT NULL),
        CONSTRAINT CK_fact_Trades_side          CHECK (side IN ('B','S')),
        CONSTRAINT CK_fact_Trades_quantity      CHECK (quantity    > 0),
        CONSTRAINT CK_fact_Trades_price_entry   CHECK (price_entry > 0),
        CONSTRAINT CK_fact_Trades_price_exit    CHECK (price_exit IS NULL OR price_exit > 0),
        CONSTRAINT CK_fact_Trades_commission    CHECK (commission_eur >= 0),
        CONSTRAINT CK_fact_Trades_open_closed
            CHECK (
                (is_open = 1 AND time_exit IS NULL     AND price_exit IS NULL     AND gross_pnl_eur IS NULL     AND net_pnl_eur IS NULL)
             OR (is_open = 0 AND time_exit IS NOT NULL AND price_exit IS NOT NULL AND gross_pnl_eur IS NOT NULL AND net_pnl_eur IS NOT NULL)
            ),
        CONSTRAINT CK_fact_Trades_time_order
            CHECK (time_exit IS NULL OR time_exit >= time_entry)
    );

    CREATE INDEX IX_fact_Trades_trader_id_time_entry
        ON dbo.fact_Trades(trader_id, time_entry)
        INCLUDE (market_id, side, net_pnl_eur, quantity, price_entry, commission_eur);

    CREATE INDEX IX_fact_Trades_market_id_time_entry
        ON dbo.fact_Trades(market_id, time_entry)
        INCLUDE (trader_id, net_pnl_eur);

    CREATE INDEX IX_fact_Trades_is_open
        ON dbo.fact_Trades(trader_id, time_entry)
        WHERE is_open = 1;

    CREATE INDEX IX_fact_Trades_trade_date_ro
        ON dbo.fact_Trades(trade_date_ro);
END;

COMMIT;
GO

----------------------------------------------------------------------------
-- 5. Seed data (idempotent)
----------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM dbo.dim_Companies)
    INSERT INTO dbo.dim_Companies (legal_name, short_name, country_code, base_currency)
    VALUES (N'TCP Capital Management SRL', N'TCP', 'RO', 'EUR');

IF NOT EXISTS (SELECT 1 FROM dbo.dim_TradingFloors)
    INSERT INTO dbo.dim_TradingFloors (company_id, city, floor_code, is_primary_hq, opened_on)
    VALUES
        (1, N'București',   'BUC', 1, '2018-01-01'),
        (1, N'Cluj-Napoca', 'CLJ', 0, '2021-06-01');

IF NOT EXISTS (SELECT 1 FROM dbo.dim_Teams)
    INSERT INTO dbo.dim_Teams (floor_id, team_name, team_code) VALUES
        (1, N'Alpha',   'BUC-A'),
        (1, N'Bravo',   'BUC-B'),
        (1, N'Charlie', 'BUC-C'),
        (2, N'Delta',   'CLJ-D'),
        (2, N'Echo',    'CLJ-E'),
        (2, N'Foxtrot', 'CLJ-F');

IF NOT EXISTS (SELECT 1 FROM dbo.dim_Sessions)
    INSERT INTO dbo.dim_Sessions (session_code, display_name, start_time_local, end_time_local) VALUES
        ('pre_market',  N'Pre-market',  '07:00', '09:30'),
        ('regular',     N'Regular',     '09:30', '17:30'),
        ('after_hours', N'After-hours', '17:30', '22:00');

IF NOT EXISTS (SELECT 1 FROM dbo.dim_OrderType)
    INSERT INTO dbo.dim_OrderType (order_type_code, display_name, is_directional) VALUES
        ('market',     N'Market',     1),
        ('limit',      N'Limit',      1),
        ('stop',       N'Stop',       1),
        ('stop_limit', N'Stop-limit', 1);

IF NOT EXISTS (SELECT 1 FROM dbo.dim_Markets)
BEGIN
    INSERT INTO dbo.dim_Markets (symbol, display_name, asset_class, quote_currency, tick_size) VALUES
     -- Equities
     ('AAPL',    N'Apple Inc.',                'equity',   'USD', 0.01),
     ('MSFT',    N'Microsoft Corp.',           'equity',   'USD', 0.01),
     ('GOOGL',   N'Alphabet Inc. Class A',     'equity',   'USD', 0.01),
     ('AMZN',    N'Amazon.com Inc.',           'equity',   'USD', 0.01),
     ('META',    N'Meta Platforms Inc.',       'equity',   'USD', 0.01),
     ('TSLA',    N'Tesla Inc.',                'equity',   'USD', 0.01),
     ('NVDA',    N'NVIDIA Corp.',              'equity',   'USD', 0.01),
     ('JPM',     N'JPMorgan Chase & Co.',      'equity',   'USD', 0.01),
     ('XOM',     N'Exxon Mobil Corp.',         'equity',   'USD', 0.01),
     ('SPY',     N'SPDR S&P 500 ETF',          'equity',   'USD', 0.01),
     -- FX
     ('EURUSD',  N'Euro / US Dollar',          'fx',       'USD', 0.00001),
     ('GBPUSD',  N'British Pound / USD',       'fx',       'USD', 0.00001),
     ('USDJPY',  N'USD / Japanese Yen',        'fx',       'JPY', 0.001),
     ('USDCHF',  N'USD / Swiss Franc',         'fx',       'CHF', 0.00001),
     ('AUDUSD',  N'Australian Dollar / USD',   'fx',       'USD', 0.00001),
     ('EURGBP',  N'Euro / British Pound',      'fx',       'GBP', 0.00001),
     ('EURJPY',  N'Euro / Japanese Yen',       'fx',       'JPY', 0.001),
     ('USDRON',  N'USD / Romanian Leu',        'fx',       'RON', 0.0001),
     -- Crypto
     ('BTCUSD',  N'Bitcoin / USD',             'crypto',   'USD', 0.01),
     ('ETHUSD',  N'Ethereum / USD',            'crypto',   'USD', 0.01),
     ('SOLUSD',  N'Solana / USD',              'crypto',   'USD', 0.0001),
     ('ADAUSD',  N'Cardano / USD',             'crypto',   'USD', 0.00001),
     ('XRPUSD',  N'XRP / USD',                 'crypto',   'USD', 0.00001),
     ('DOGEUSD', N'Dogecoin / USD',            'crypto',   'USD', 0.000001),
     -- Commodities
     ('XAUUSD',  N'Gold Spot / USD',           'commodity','USD', 0.01),
     ('XAGUSD',  N'Silver Spot / USD',         'commodity','USD', 0.001),
     ('WTI',     N'WTI Crude Oil',             'commodity','USD', 0.01),
     ('BRENT',   N'Brent Crude Oil',           'commodity','USD', 0.01),
     ('NATGAS',  N'Natural Gas',               'commodity','USD', 0.001),
     ('COPPER',  N'High-Grade Copper',         'commodity','USD', 0.0005);
END;

IF NOT EXISTS (SELECT 1 FROM dbo.config_Capital)
    INSERT INTO dbo.config_Capital (trader_id, amount_eur, effective_from, effective_to, note)
    VALUES (NULL, 80000.00, '2024-01-01T00:00:00+02:00', NULL, N'Initial global baseline.');

----------------------------------------------------------------------------
-- 6. dim_Date population (2024-01-01 .. 2030-12-31)
----------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM dbo.dim_Date)
BEGIN
    -- ISO conventions: Monday = day 1.
    SET DATEFIRST 1;

    ;WITH n0 AS (SELECT 1 AS x UNION ALL SELECT 1 AS x),
         n1 AS (SELECT 1 AS x FROM n0 a, n0 b),    -- 4
         n2 AS (SELECT 1 AS x FROM n1 a, n1 b),    -- 16
         n3 AS (SELECT 1 AS x FROM n2 a, n2 b),    -- 256
         n4 AS (SELECT 1 AS x FROM n3 a, n3 b),    -- 65 536
         tally AS (
            SELECT TOP (DATEDIFF(DAY, '2024-01-01', '2030-12-31') + 1)
                ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS d_offset
            FROM n4
         )
    INSERT INTO dbo.dim_Date
        (date_id, calendar_date, iso_year, iso_week, [year], [quarter], [month],
         month_name_ro, month_name_en, day_of_month, day_of_week, is_weekday, is_ro_holiday,
         ro_holiday_name, en_holiday_name)
        -- fiscal_year is a PERSISTED computed column (= [year]); not in the column list.
    SELECT
        CONVERT(INT, CONVERT(VARCHAR(8), DATEADD(DAY, t.d_offset, '2024-01-01'), 112)) AS date_id,
        DATEADD(DAY, t.d_offset, '2024-01-01')                                          AS calendar_date,
        -- ISO-8601 year of the Thursday in the same ISO week.
        DATEPART(YEAR, DATEADD(DAY, 26 - DATEPART(ISO_WEEK, DATEADD(DAY, t.d_offset, '2024-01-01')),
                                       DATEADD(DAY, t.d_offset, '2024-01-01')))         AS iso_year,
        DATEPART(ISO_WEEK, DATEADD(DAY, t.d_offset, '2024-01-01'))                       AS iso_week,
        DATEPART(YEAR,     DATEADD(DAY, t.d_offset, '2024-01-01'))                       AS [year],
        DATEPART(QUARTER,  DATEADD(DAY, t.d_offset, '2024-01-01'))                       AS [quarter],
        DATEPART(MONTH,    DATEADD(DAY, t.d_offset, '2024-01-01'))                       AS [month],
        CASE DATEPART(MONTH, DATEADD(DAY, t.d_offset, '2024-01-01'))
            WHEN  1 THEN N'Ianuarie'  WHEN  2 THEN N'Februarie' WHEN  3 THEN N'Martie'
            WHEN  4 THEN N'Aprilie'   WHEN  5 THEN N'Mai'       WHEN  6 THEN N'Iunie'
            WHEN  7 THEN N'Iulie'     WHEN  8 THEN N'August'    WHEN  9 THEN N'Septembrie'
            WHEN 10 THEN N'Octombrie' WHEN 11 THEN N'Noiembrie' WHEN 12 THEN N'Decembrie'
        END                                                                              AS month_name_ro,
        CASE DATEPART(MONTH, DATEADD(DAY, t.d_offset, '2024-01-01'))
            WHEN  1 THEN N'January'   WHEN  2 THEN N'February'  WHEN  3 THEN N'March'
            WHEN  4 THEN N'April'     WHEN  5 THEN N'May'       WHEN  6 THEN N'June'
            WHEN  7 THEN N'July'      WHEN  8 THEN N'August'    WHEN  9 THEN N'September'
            WHEN 10 THEN N'October'   WHEN 11 THEN N'November'  WHEN 12 THEN N'December'
        END                                                                              AS month_name_en,
        DATEPART(DAY,      DATEADD(DAY, t.d_offset, '2024-01-01'))                       AS day_of_month,
        -- With DATEFIRST 1, DATEPART(WEEKDAY) returns 1=Mon..7=Sun directly.
        DATEPART(WEEKDAY,  DATEADD(DAY, t.d_offset, '2024-01-01'))                       AS day_of_week,
        CASE WHEN DATEPART(WEEKDAY, DATEADD(DAY, t.d_offset, '2024-01-01')) BETWEEN 1 AND 5
             THEN 1 ELSE 0 END                                                            AS is_weekday,
        0                                                                                  AS is_ro_holiday,
        NULL                                                                               AS ro_holiday_name,
        NULL                                                                               AS en_holiday_name
    FROM tally AS t;

    -- Romanian public holidays 2024-2030 (fixed + key movable) per Codul Muncii art. 139.
    -- Movable (Orthodox Easter Friday/Sunday/Monday, Pentecost Sunday/Monday): hand-curated dates per year.
    -- PRIMARY KEY catches duplicate-date collisions at insert time (e.g. Pentecost Monday + Children's Day on the same calendar day).
    -- The 2026-06-01 collision between Rusalii (Pentecost Monday) and Ziua Copilului (Children's Day) is materialised as a single dim_Date row with `ro_holiday_name = N'Rusalii / Ziua Copilului'` (Codul Muncii observes both).
    DECLARE @holidays TABLE (h_date DATE PRIMARY KEY, h_name NVARCHAR(80), h_name_en NVARCHAR(80));
    INSERT INTO @holidays VALUES
        -- 2024
        ('2024-01-01', N'Anul Nou',                     N'New Year''s Day'),
        ('2024-01-02', N'Anul Nou',                     N'New Year''s Day (day 2)'),
        ('2024-01-06', N'Bobotează',                    N'Epiphany'),
        ('2024-01-07', N'Sfântul Ion',                  N'Saint John the Baptist'),
        ('2024-01-24', N'Unirea Principatelor Române',  N'Union of the Romanian Principalities'),
        ('2024-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2024-05-03', N'Vinerea Mare',                 N'Good Friday'),
        ('2024-05-05', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2024-05-06', N'Paștele',                      N'Orthodox Easter Monday'),
        ('2024-06-01', N'Ziua Copilului',               N'Children''s Day'),
        ('2024-06-23', N'Rusalii',                      N'Pentecost'),
        ('2024-06-24', N'Rusalii',                      N'Pentecost Monday'),
        ('2024-08-15', N'Adormirea Maicii Domnului',    N'Dormition of the Mother of God'),
        ('2024-11-30', N'Sfântul Andrei',               N'Saint Andrew'),
        ('2024-12-01', N'Ziua Națională a României',    N'Romania National Day'),
        ('2024-12-25', N'Crăciunul',                    N'Christmas Day'),
        ('2024-12-26', N'Crăciunul',                    N'Christmas Day (day 2)'),
        -- 2025
        ('2025-01-01', N'Anul Nou',                     N'New Year''s Day'),
        ('2025-01-02', N'Anul Nou',                     N'New Year''s Day (day 2)'),
        ('2025-01-06', N'Bobotează',                    N'Epiphany'),
        ('2025-01-07', N'Sfântul Ion',                  N'Saint John the Baptist'),
        ('2025-01-24', N'Unirea Principatelor Române',  N'Union of the Romanian Principalities'),
        ('2025-04-18', N'Vinerea Mare',                 N'Good Friday'),
        ('2025-04-20', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2025-04-21', N'Paștele',                      N'Orthodox Easter Monday'),
        ('2025-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2025-06-01', N'Ziua Copilului',               N'Children''s Day'),
        ('2025-06-08', N'Rusalii',                      N'Pentecost'),
        ('2025-06-09', N'Rusalii',                      N'Pentecost Monday'),
        ('2025-08-15', N'Adormirea Maicii Domnului',    N'Dormition of the Mother of God'),
        ('2025-11-30', N'Sfântul Andrei',               N'Saint Andrew'),
        ('2025-12-01', N'Ziua Națională a României',    N'Romania National Day'),
        ('2025-12-25', N'Crăciunul',                    N'Christmas Day'),
        ('2025-12-26', N'Crăciunul',                    N'Christmas Day (day 2)'),
        -- 2026 — Pentecost Monday (June 1) collides with Children's Day; collapsed to a combined label.
        ('2026-01-01', N'Anul Nou',                     N'New Year''s Day'),
        ('2026-01-02', N'Anul Nou',                     N'New Year''s Day (day 2)'),
        ('2026-01-06', N'Bobotează',                    N'Epiphany'),
        ('2026-01-07', N'Sfântul Ion',                  N'Saint John the Baptist'),
        ('2026-01-24', N'Unirea Principatelor Române',  N'Union of the Romanian Principalities'),
        ('2026-04-10', N'Vinerea Mare',                 N'Good Friday'),
        ('2026-04-12', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2026-04-13', N'Paștele',                      N'Orthodox Easter Monday'),
        ('2026-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2026-05-31', N'Rusalii',                      N'Pentecost'),
        ('2026-06-01', N'Rusalii / Ziua Copilului',     N'Pentecost Monday / Children''s Day'),
        ('2026-08-15', N'Adormirea Maicii Domnului',    N'Dormition of the Mother of God'),
        ('2026-11-30', N'Sfântul Andrei',               N'Saint Andrew'),
        ('2026-12-01', N'Ziua Națională a României',    N'Romania National Day'),
        ('2026-12-25', N'Crăciunul',                    N'Christmas Day'),
        ('2026-12-26', N'Crăciunul',                    N'Christmas Day (day 2)'),
        -- 2027
        ('2027-01-01', N'Anul Nou',                     N'New Year''s Day'),
        ('2027-01-02', N'Anul Nou',                     N'New Year''s Day (day 2)'),
        ('2027-01-06', N'Bobotează',                    N'Epiphany'),
        ('2027-01-07', N'Sfântul Ion',                  N'Saint John the Baptist'),
        ('2027-01-24', N'Unirea Principatelor Române',  N'Union of the Romanian Principalities'),
        ('2027-04-30', N'Vinerea Mare',                 N'Good Friday'),
        ('2027-05-02', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2027-05-03', N'Paștele',                      N'Orthodox Easter Monday'),
        ('2027-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2027-06-01', N'Ziua Copilului',               N'Children''s Day'),
        ('2027-06-20', N'Rusalii',                      N'Pentecost'),
        ('2027-06-21', N'Rusalii',                      N'Pentecost Monday'),
        ('2027-08-15', N'Adormirea Maicii Domnului',    N'Dormition of the Mother of God'),
        ('2027-11-30', N'Sfântul Andrei',               N'Saint Andrew'),
        ('2027-12-01', N'Ziua Națională a României',    N'Romania National Day'),
        ('2027-12-25', N'Crăciunul',                    N'Christmas Day'),
        ('2027-12-26', N'Crăciunul',                    N'Christmas Day (day 2)'),
        -- 2028
        ('2028-01-01', N'Anul Nou',                     N'New Year''s Day'),
        ('2028-01-02', N'Anul Nou',                     N'New Year''s Day (day 2)'),
        ('2028-01-06', N'Bobotează',                    N'Epiphany'),
        ('2028-01-07', N'Sfântul Ion',                  N'Saint John the Baptist'),
        ('2028-01-24', N'Unirea Principatelor Române',  N'Union of the Romanian Principalities'),
        ('2028-04-14', N'Vinerea Mare',                 N'Good Friday'),
        ('2028-04-16', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2028-04-17', N'Paștele',                      N'Orthodox Easter Monday'),
        ('2028-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2028-06-01', N'Ziua Copilului',               N'Children''s Day'),
        ('2028-06-04', N'Rusalii',                      N'Pentecost'),
        ('2028-06-05', N'Rusalii',                      N'Pentecost Monday'),
        ('2028-08-15', N'Adormirea Maicii Domnului',    N'Dormition of the Mother of God'),
        ('2028-11-30', N'Sfântul Andrei',               N'Saint Andrew'),
        ('2028-12-01', N'Ziua Națională a României',    N'Romania National Day'),
        ('2028-12-25', N'Crăciunul',                    N'Christmas Day'),
        ('2028-12-26', N'Crăciunul',                    N'Christmas Day (day 2)'),
        -- 2029
        ('2029-01-01', N'Anul Nou',                     N'New Year''s Day'),
        ('2029-01-02', N'Anul Nou',                     N'New Year''s Day (day 2)'),
        ('2029-01-06', N'Bobotează',                    N'Epiphany'),
        ('2029-01-07', N'Sfântul Ion',                  N'Saint John the Baptist'),
        ('2029-01-24', N'Unirea Principatelor Române',  N'Union of the Romanian Principalities'),
        ('2029-04-06', N'Vinerea Mare',                 N'Good Friday'),
        ('2029-04-08', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2029-04-09', N'Paștele',                      N'Orthodox Easter Monday'),
        ('2029-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2029-05-27', N'Rusalii',                      N'Pentecost'),
        ('2029-05-28', N'Rusalii',                      N'Pentecost Monday'),
        ('2029-06-01', N'Ziua Copilului',               N'Children''s Day'),
        ('2029-08-15', N'Adormirea Maicii Domnului',    N'Dormition of the Mother of God'),
        ('2029-11-30', N'Sfântul Andrei',               N'Saint Andrew'),
        ('2029-12-01', N'Ziua Națională a României',    N'Romania National Day'),
        ('2029-12-25', N'Crăciunul',                    N'Christmas Day'),
        ('2029-12-26', N'Crăciunul',                    N'Christmas Day (day 2)'),
        -- 2030
        ('2030-01-01', N'Anul Nou',                     N'New Year''s Day'),
        ('2030-01-02', N'Anul Nou',                     N'New Year''s Day (day 2)'),
        ('2030-01-06', N'Bobotează',                    N'Epiphany'),
        ('2030-01-07', N'Sfântul Ion',                  N'Saint John the Baptist'),
        ('2030-01-24', N'Unirea Principatelor Române',  N'Union of the Romanian Principalities'),
        ('2030-04-26', N'Vinerea Mare',                 N'Good Friday'),
        ('2030-04-28', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2030-04-29', N'Paștele',                      N'Orthodox Easter Monday'),
        ('2030-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2030-06-01', N'Ziua Copilului',               N'Children''s Day'),
        ('2030-06-16', N'Rusalii',                      N'Pentecost'),
        ('2030-06-17', N'Rusalii',                      N'Pentecost Monday'),
        ('2030-08-15', N'Adormirea Maicii Domnului',    N'Dormition of the Mother of God'),
        ('2030-11-30', N'Sfântul Andrei',               N'Saint Andrew'),
        ('2030-12-01', N'Ziua Națională a României',    N'Romania National Day'),
        ('2030-12-25', N'Crăciunul',                    N'Christmas Day'),
        ('2030-12-26', N'Crăciunul',                    N'Christmas Day (day 2)');

    UPDATE d
       SET d.is_ro_holiday   = 1,
           d.ro_holiday_name = h.h_name,
           d.en_holiday_name = h.h_name_en
    FROM dbo.dim_Date  AS d
    JOIN @holidays     AS h ON h.h_date = d.calendar_date;
END;
GO

----------------------------------------------------------------------------
-- 7. Functions
----------------------------------------------------------------------------
CREATE OR ALTER FUNCTION dbo.fn_GetCapitalBaseline
(
    @trader_id INT,
    @as_of     DATETIMEOFFSET(3)
)
RETURNS DECIMAL(18,2)
WITH SCHEMABINDING
AS
BEGIN
    DECLARE @amount DECIMAL(18,2);

    SELECT TOP 1 @amount = c.amount_eur
    FROM dbo.config_Capital AS c
    WHERE (c.trader_id = @trader_id OR c.trader_id IS NULL)
      AND c.effective_from <= @as_of
      AND (c.effective_to IS NULL OR c.effective_to > @as_of)
    ORDER BY CASE WHEN c.trader_id = @trader_id THEN 0 ELSE 1 END,
             c.effective_from DESC;

    RETURN @amount;
END;
GO

CREATE OR ALTER FUNCTION dbo.fn_IsTradingDay
(
    @d DATE
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    DECLARE @result BIT = 0;

    SELECT @result =
        CASE WHEN d.is_weekday = 1 AND d.is_ro_holiday = 0 THEN 1 ELSE 0 END
    FROM dbo.dim_Date AS d
    WHERE d.calendar_date = @d;

    RETURN ISNULL(@result, 0);
END;
GO

CREATE OR ALTER FUNCTION dbo.fn_PreviousBusinessDay
(
    @d DATE
)
RETURNS DATE
WITH SCHEMABINDING
AS
BEGIN
    DECLARE @prev DATE;

    SELECT TOP 1 @prev = d.calendar_date
    FROM dbo.dim_Date AS d
    WHERE d.calendar_date < @d
      AND d.is_weekday    = 1
      AND d.is_ro_holiday = 0
    ORDER BY d.calendar_date DESC;

    RETURN @prev;
END;
GO

----------------------------------------------------------------------------
-- 8. Views
----------------------------------------------------------------------------
CREATE OR ALTER VIEW dbo.v_trades_enriched
WITH SCHEMABINDING
AS
SELECT
    f.trade_uid,
    f.trade_date_ro,
    f.time_entry,
    f.time_exit,
    f.trader_id,
    e.first_name + N' ' + e.last_name                                 AS trader_full_name,
    e.team_id,
    t.team_name,
    e.floor_id,
    tf.city                                                            AS floor_city,
    f.market_id,
    m.symbol,
    m.asset_class,
    s.session_code,
    o.order_type_code,
    f.side,
    f.quantity,
    f.price_entry,
    f.price_exit,
    f.gross_pnl_eur,
    f.commission_eur,
    f.net_pnl_eur,
    f.is_open,
    CASE WHEN f.time_exit IS NULL THEN NULL ELSE DATEDIFF(MINUTE, f.time_entry, f.time_exit) END AS holding_time_minutes,
    CASE WHEN f.quantity = 0 OR f.net_pnl_eur IS NULL THEN NULL ELSE f.net_pnl_eur / f.quantity END AS pnl_per_unit
FROM dbo.fact_Trades            AS f
JOIN dbo.dim_Employees          AS e  ON e.employee_id  = f.trader_id
JOIN dbo.dim_Teams              AS t  ON t.team_id      = e.team_id
JOIN dbo.dim_TradingFloors      AS tf ON tf.floor_id    = e.floor_id
JOIN dbo.dim_Markets            AS m  ON m.market_id    = f.market_id
JOIN dbo.dim_Sessions           AS s  ON s.session_id   = f.session_id
JOIN dbo.dim_OrderType          AS o  ON o.order_type_id = f.order_type_id;
GO

CREATE OR ALTER VIEW dbo.v_employee_performance
WITH SCHEMABINDING
AS
SELECT
    v.trade_date_ro,
    v.trader_id                                                       AS employee_id,
    MAX(v.trader_full_name)                                            AS trader_full_name,
    MAX(v.team_id)                                                     AS team_id,
    MAX(v.floor_id)                                                    AS floor_id,
    COUNT_BIG(*)                                                       AS trade_count,
    SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1 ELSE 0 END)                 AS win_count,
    SUM(CASE WHEN v.net_pnl_eur < 0 THEN 1 ELSE 0 END)                 AS loss_count,
    CAST(SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1.0 ELSE 0 END)
         / NULLIF(COUNT_BIG(*), 0) AS DECIMAL(9,6))                    AS win_rate,
    SUM(v.gross_pnl_eur)                                               AS gross_pnl_eur_total,
    SUM(v.commission_eur)                                              AS commission_eur_total,
    SUM(v.net_pnl_eur)                                                 AS net_pnl_eur_total,
    AVG(CAST(v.holding_time_minutes AS DECIMAL(18,4)))                 AS avg_holding_time_minutes
FROM dbo.v_trades_enriched AS v
WHERE v.is_open = 0
GROUP BY v.trade_date_ro, v.trader_id;
GO

CREATE OR ALTER VIEW dbo.v_team_performance
WITH SCHEMABINDING
AS
SELECT
    v.trade_date_ro,
    v.team_id,
    MAX(v.team_name)                                                   AS team_name,
    MAX(v.floor_id)                                                    AS floor_id,
    COUNT_BIG(*)                                                       AS trade_count,
    SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1 ELSE 0 END)                 AS win_count,
    SUM(CASE WHEN v.net_pnl_eur < 0 THEN 1 ELSE 0 END)                 AS loss_count,
    CAST(SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1.0 ELSE 0 END)
         / NULLIF(COUNT_BIG(*), 0) AS DECIMAL(9,6))                    AS win_rate,
    SUM(v.gross_pnl_eur)                                               AS gross_pnl_eur_total,
    SUM(v.commission_eur)                                              AS commission_eur_total,
    SUM(v.net_pnl_eur)                                                 AS net_pnl_eur_total,
    AVG(CAST(v.holding_time_minutes AS DECIMAL(18,4)))                 AS avg_holding_time_minutes
FROM dbo.v_trades_enriched AS v
WHERE v.is_open = 0
GROUP BY v.trade_date_ro, v.team_id;
GO

CREATE OR ALTER VIEW dbo.v_floor_performance
WITH SCHEMABINDING
AS
SELECT
    v.trade_date_ro,
    v.floor_id,
    MAX(v.floor_city)                                                  AS floor_city,
    COUNT_BIG(*)                                                       AS trade_count,
    SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1 ELSE 0 END)                 AS win_count,
    SUM(CASE WHEN v.net_pnl_eur < 0 THEN 1 ELSE 0 END)                 AS loss_count,
    CAST(SUM(CASE WHEN v.net_pnl_eur > 0 THEN 1.0 ELSE 0 END)
         / NULLIF(COUNT_BIG(*), 0) AS DECIMAL(9,6))                    AS win_rate,
    SUM(v.gross_pnl_eur)                                               AS gross_pnl_eur_total,
    SUM(v.commission_eur)                                              AS commission_eur_total,
    SUM(v.net_pnl_eur)                                                 AS net_pnl_eur_total,
    AVG(CAST(v.holding_time_minutes AS DECIMAL(18,4)))                 AS avg_holding_time_minutes
FROM dbo.v_trades_enriched AS v
WHERE v.is_open = 0
GROUP BY v.trade_date_ro, v.floor_id;
GO

CREATE OR ALTER VIEW dbo.v_daily_pnl
WITH SCHEMABINDING
AS
SELECT
    ep.employee_id,
    ep.trader_full_name,
    ep.team_id,
    ep.floor_id,
    ep.trade_date_ro,
    ep.trade_count,
    ep.net_pnl_eur_total,
    SUM(ep.net_pnl_eur_total) OVER (
        PARTITION BY ep.employee_id
        ORDER BY ep.trade_date_ro
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )                                                                  AS cumulative_net_pnl_eur
FROM dbo.v_employee_performance AS ep;
GO

-- Inline TVF: risk metrics per (employee_id, period). Backs Sharpe, Sortino,
-- VaR-95, downside-stdev, and total PnL.
CREATE OR ALTER FUNCTION dbo.tvf_RiskMetrics
(
    @employee_id INT,
    @from        DATE,
    @to          DATE
)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT
        COUNT_BIG(*)                                                                AS trading_days,
        AVG(ep.net_pnl_eur_total)                                                   AS mean_daily_pnl,
        STDEV(ep.net_pnl_eur_total)                                                 AS stdev_daily_pnl,
        STDEV(CASE WHEN ep.net_pnl_eur_total < 0 THEN ep.net_pnl_eur_total END)     AS stdev_downside,
        PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY ep.net_pnl_eur_total) OVER ()  AS var_95,
        SUM(ep.net_pnl_eur_total)                                                   AS total_net_pnl
    FROM dbo.v_employee_performance AS ep
    WHERE ep.employee_id    = @employee_id
      AND ep.trade_date_ro BETWEEN @from AND @to;
GO

-- Inline TVF capital baseline (MJ-02 optimizer-friendly alternative).
CREATE OR ALTER FUNCTION dbo.tvf_GetCapitalBaseline
(
    @trader_id INT,
    @as_of     DATETIMEOFFSET(3)
)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT TOP 1 c.amount_eur
    FROM dbo.config_Capital AS c
    WHERE (c.trader_id = @trader_id OR c.trader_id IS NULL)
      AND c.effective_from <= @as_of
      AND (c.effective_to IS NULL OR c.effective_to > @as_of)
    ORDER BY CASE WHEN c.trader_id = @trader_id THEN 0 ELSE 1 END,
             c.effective_from DESC;
GO

----------------------------------------------------------------------------
-- 9. Stored procedures
----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.usp_GenerateDailyTrades
    @trade_date DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @trade_date IS NULL
        THROW 50001, 'usp_GenerateDailyTrades: @trade_date is required.', 1;

    IF dbo.fn_IsTradingDay(@trade_date) = 0
        THROW 50002, 'usp_GenerateDailyTrades: @trade_date is not a trading day.', 1;

    BEGIN TRY
        BEGIN TRAN;

        IF EXISTS (
            SELECT 1
            FROM dbo.fact_Trades
            WHERE trade_date_ro = @trade_date
        )
        BEGIN
            IF @@TRANCOUNT > 0 ROLLBACK;
            SELECT 0 AS rows_inserted, 'already_generated' AS status;
            RETURN 0;
        END;

        DECLARE @inserted INT = 0;
        -- Body intentionally minimal; the generator worker performs the bulk
        -- INSERT in this transaction via sp_executesql or a TVP child proc
        -- delivered in V002__synth_tvp.sql.

        COMMIT;

        SELECT @inserted AS rows_inserted, 'inserted' AS status;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK;
        THROW;
    END CATCH
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_GetEmployeePerformance
    @employee_id INT,
    @from        DATE,
    @to          DATE
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @employee_id IS NULL OR @from IS NULL OR @to IS NULL
        THROW 50010, 'usp_GetEmployeePerformance: all parameters are required.', 1;
    IF @from > @to
        THROW 50011, 'usp_GetEmployeePerformance: @from must be <= @to.', 1;

    SELECT
        ep.trade_date_ro,
        ep.employee_id,
        ep.trader_full_name,
        ep.team_id,
        ep.floor_id,
        ep.trade_count,
        ep.win_count,
        ep.loss_count,
        ep.win_rate,
        ep.gross_pnl_eur_total,
        ep.commission_eur_total,
        ep.net_pnl_eur_total,
        ep.avg_holding_time_minutes
    FROM dbo.v_employee_performance AS ep
    WHERE ep.employee_id    = @employee_id
      AND ep.trade_date_ro >= @from
      AND ep.trade_date_ro <= @to
    ORDER BY ep.trade_date_ro;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_GetTopPerformers
    @scope NVARCHAR(20),
    @from  DATE,
    @to    DATE,
    @top_n INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @scope NOT IN (N'trader', N'team', N'floor')
        THROW 50020, 'usp_GetTopPerformers: @scope must be one of trader/team/floor.', 1;
    IF @from IS NULL OR @to IS NULL OR @from > @to
        THROW 50021, 'usp_GetTopPerformers: invalid date range.', 1;
    IF @top_n IS NULL OR @top_n <= 0 OR @top_n > 100
        THROW 50022, 'usp_GetTopPerformers: @top_n must be between 1 and 100.', 1;

    IF @scope = N'trader'
    BEGIN
        SELECT TOP (@top_n)
            ep.employee_id                 AS entity_id,
            MAX(ep.trader_full_name)       AS entity_name,
            SUM(ep.net_pnl_eur_total)      AS net_pnl_eur_total,
            SUM(ep.trade_count)            AS trade_count,
            SUM(ep.win_count) * 1.0 / NULLIF(SUM(ep.trade_count), 0) AS win_rate
        FROM dbo.v_employee_performance AS ep
        WHERE ep.trade_date_ro BETWEEN @from AND @to
        GROUP BY ep.employee_id
        ORDER BY net_pnl_eur_total DESC;
    END
    ELSE IF @scope = N'team'
    BEGIN
        SELECT TOP (@top_n)
            tp.team_id                     AS entity_id,
            MAX(tp.team_name)              AS entity_name,
            SUM(tp.net_pnl_eur_total)      AS net_pnl_eur_total,
            SUM(tp.trade_count)            AS trade_count,
            SUM(tp.win_count) * 1.0 / NULLIF(SUM(tp.trade_count), 0) AS win_rate
        FROM dbo.v_team_performance AS tp
        WHERE tp.trade_date_ro BETWEEN @from AND @to
        GROUP BY tp.team_id
        ORDER BY net_pnl_eur_total DESC;
    END
    ELSE
    BEGIN
        SELECT TOP (@top_n)
            fp.floor_id                    AS entity_id,
            MAX(fp.floor_city)             AS entity_name,
            SUM(fp.net_pnl_eur_total)      AS net_pnl_eur_total,
            SUM(fp.trade_count)            AS trade_count,
            SUM(fp.win_count) * 1.0 / NULLIF(SUM(fp.trade_count), 0) AS win_rate
        FROM dbo.v_floor_performance AS fp
        WHERE fp.trade_date_ro BETWEEN @from AND @to
        GROUP BY fp.floor_id
        ORDER BY net_pnl_eur_total DESC;
    END
END;
GO

----------------------------------------------------------------------------
-- 10. Row-level security
----------------------------------------------------------------------------
CREATE OR ALTER FUNCTION rls.fn_TradesPredicate(@trader_id_in_row INT)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS result
    FROM (
        -- p = principal row (the calling user resolved from SESSION_CONTEXT)
        SELECT TOP 1
            ur.scope,
            ur.employee_id      AS principal_employee_id,
            e.team_id           AS principal_team,
            e.floor_id          AS principal_floor
        FROM dbo.dim_UserRoles AS ur
        LEFT JOIN dbo.dim_Employees AS e
            ON e.employee_id = ur.employee_id
        WHERE ur.is_active = 1
          AND ur.aad_object_id = CAST(SESSION_CONTEXT(N'aad_object_id') AS UNIQUEIDENTIFIER)
    ) AS p
    WHERE
           p.scope = 'admin'
        OR (p.scope = 'trader' AND p.principal_employee_id = @trader_id_in_row)
        OR EXISTS (
            -- t = trader row of the fact row
            SELECT 1 FROM dbo.dim_Employees AS t
            WHERE t.employee_id = @trader_id_in_row
              AND (
                   (p.scope = 'team_lead'     AND t.team_id  = p.principal_team)
                OR (p.scope = 'floor_manager' AND t.floor_id = p.principal_floor)
              )
        );
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.security_policies
    WHERE [name] = N'TradesAccessPolicy'
      AND [schema_id] = SCHEMA_ID(N'rls')
)
BEGIN
    EXEC(N'
        CREATE SECURITY POLICY rls.TradesAccessPolicy
        ADD FILTER PREDICATE rls.fn_TradesPredicate(trader_id) ON dbo.fact_Trades,
        ADD BLOCK  PREDICATE rls.fn_TradesPredicate(trader_id) ON dbo.fact_Trades AFTER INSERT,
        ADD BLOCK  PREDICATE rls.fn_TradesPredicate(trader_id) ON dbo.fact_Trades AFTER UPDATE
        WITH (STATE = ON);
    ');
END;
GO

----------------------------------------------------------------------------
-- 11. Custom roles & grants
----------------------------------------------------------------------------
IF DATABASE_PRINCIPAL_ID('tcp_ai_assistant') IS NULL
    CREATE ROLE tcp_ai_assistant AUTHORIZATION dbo;
IF DATABASE_PRINCIPAL_ID('tcp_generator') IS NULL
    CREATE ROLE tcp_generator AUTHORIZATION dbo;
IF DATABASE_PRINCIPAL_ID('tcp_bi_reader') IS NULL
    CREATE ROLE tcp_bi_reader AUTHORIZATION dbo;
IF DATABASE_PRINCIPAL_ID('tcp_admin') IS NULL
    CREATE ROLE tcp_admin AUTHORIZATION dbo;

-- tcp_ai_assistant: views + read-only sprocs + the two RLS-predicate-referenced tables.
GRANT EXECUTE ON dbo.usp_GetEmployeePerformance TO tcp_ai_assistant;
GRANT EXECUTE ON dbo.usp_GetTopPerformers       TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_trades_enriched          TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_employee_performance     TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_team_performance         TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_floor_performance        TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_daily_pnl                TO tcp_ai_assistant;
GRANT SELECT  ON dbo.dim_UserRoles              TO tcp_ai_assistant;  -- RLS predicate
GRANT SELECT  ON dbo.dim_Employees              TO tcp_ai_assistant;  -- RLS predicate

-- tcp_generator: write fact_Trades, read dims + config, execute calendar/capital fns.
GRANT EXECUTE ON dbo.usp_GenerateDailyTrades   TO tcp_generator;
GRANT INSERT, UPDATE ON dbo.fact_Trades        TO tcp_generator;
GRANT SELECT ON dbo.dim_Employees              TO tcp_generator;
GRANT SELECT ON dbo.dim_Accounts               TO tcp_generator;
GRANT SELECT ON dbo.dim_Markets                TO tcp_generator;
GRANT SELECT ON dbo.dim_Sessions               TO tcp_generator;
GRANT SELECT ON dbo.dim_OrderType              TO tcp_generator;
GRANT SELECT ON dbo.dim_Date                   TO tcp_generator;
GRANT SELECT ON dbo.dim_UserRoles              TO tcp_generator;     -- RLS predicate
GRANT SELECT ON dbo.config_Capital             TO tcp_generator;
GRANT EXECUTE ON dbo.fn_GetCapitalBaseline     TO tcp_generator;
GRANT EXECUTE ON dbo.fn_IsTradingDay           TO tcp_generator;
GRANT EXECUTE ON dbo.fn_PreviousBusinessDay    TO tcp_generator;

-- tcp_bi_reader: PowerBI Service principal — views + dim tables (PowerBI relationship engine).
GRANT SELECT ON dbo.v_trades_enriched      TO tcp_bi_reader;
GRANT SELECT ON dbo.v_employee_performance TO tcp_bi_reader;
GRANT SELECT ON dbo.v_team_performance     TO tcp_bi_reader;
GRANT SELECT ON dbo.v_floor_performance    TO tcp_bi_reader;
GRANT SELECT ON dbo.v_daily_pnl            TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Employees          TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Teams              TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_TradingFloors      TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Markets            TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Sessions           TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_OrderType          TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Date               TO tcp_bi_reader;

-- tcp_admin: full read/write/EXECUTE (thesis-context).
ALTER ROLE db_datareader ADD MEMBER tcp_admin;
ALTER ROLE db_datawriter ADD MEMBER tcp_admin;
GRANT EXECUTE ON SCHEMA::dbo TO tcp_admin;

----------------------------------------------------------------------------
-- 12. Record the migration
----------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM dbo.schema_history WHERE script_name = 'V001__init.sql')
    INSERT INTO dbo.schema_history (script_name, applied_at_utc, checksum)
    VALUES ('V001__init.sql', SYSUTCDATETIME(), 'TODO-checksum-set-by-CI');  -- CI: SHA256 placeholder

PRINT 'V001__init.sql applied successfully.';
```

---

## 16. Open questions

1. **`01_business_requirements.md` — KPI catalogue.** This document assumes the headline KPIs are `net_pnl_eur_total`, `win_rate`, `trade_count`, `avg_holding_time_minutes`, and `cumulative_net_pnl_eur`. **Resolution:** confirm in Etapa 2's PowerBI semantic-model design.
2. **`03_architecture.md` — Function App identity.** The design grants `tcp_ai_assistant` and `tcp_generator` to *the Function App's managed identity*. If Etapa 4 splits the Function App into two separate apps (assistant vs. generator), the grants will need to map to two distinct managed identities. **Resolution:** Etapa 4 (architecture).
3. **`03_architecture.md` — PowerBI principal.** Whether PowerBI Service authenticates with a service principal (clean) or with a personal AAD account (faster for a one-person thesis) is deferred. Either path uses `tcp_bi_reader` privileges. **Resolution:** Etapa 8 (BI deployment).
4. **`01_business_requirements.md` — open trades reporting.** The current views filter out `is_open = 1` rows to keep the aggregates consistent. If the dashboard must expose mark-to-market unrealised PnL, a parallel set of views (e.g., `v_open_positions`) will be added in Etapa 5. **Resolution:** Etapa 5 (synthetic data + analytical layer).
5. **`03_architecture.md` — Key Vault references.** The bootstrap SQL admin password path through Key Vault, the AAD tenant for the contained users, and the BACPAC storage account name. **Resolution:** Etapa 4 (Bicep modules).
6. **TVP for the generator.** `V002__synth_tvp.sql` will introduce `dbo.tt_TradeBatch` (a table type) and switch `usp_GenerateDailyTrades` to consume it. The current proc is a structural placeholder. **Resolution:** Etapa 5 (data generation).

### Resolved during review pass 1

- **OQ-04 (slippage / `modeled_pnl_eur`)** — Resolved: KPI-TR-063 was removed from v1.0 in `01_BR §10` (added to the Out-of-Scope list). No `modeled_pnl_eur` column is added to `fact_Trades`.
- **`tcp_bi_reader` cross-doc gap** — Resolved: role defined in §10.2 with the appropriate SELECT grants.
- **BACPAC schedule conflict** — Resolved: weekly BACPAC export runs as a second Function App Timer Trigger at Sunday 08:00 RO (§12), reconciled with `03_arch §11`.

---

*End of document — `02_database_design.md`.*
