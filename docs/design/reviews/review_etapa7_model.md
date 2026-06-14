# Review — Etapa 7 PowerBI Semantic Model Conformance

- **Stage**: Etapa 7 (PowerBI build) — pass 1 (database-architect review)
- **Date**: 2026-05-16
- **Scope**: TMDL semantic-model definitions under `powerbi/model/` validated against the canonical DB design in `docs/design/02_database_design.md` and the implementation truth `db/migrations/V001__init.sql`.
- **Reviewer perspective**: data-layer architecture (table↔view/dim mapping, column type conformance, FK/relationship integrity, star-schema correctness, RLS, M-expression hygiene).

---

## 1. Verdict

**APPROVED with MINOR FOLLOW-UPS.** The semantic model is faithful to the DB design: every TMDL table maps to a real `dbo` object, every column maps to a real source column with a compatible PowerBI datatype, every relationship has a real underlying FK or implicit join key, the canonical date axis (`dim_Date.calendar_date`) is wired correctly, all 10 fact/dim partitions use Import mode with parameterised `Sql.Database(SqlServer, SqlDatabase)` sources, and the model declares `discourageImplicitMeasures` plus `defaultMode: import` at the database level. RLS roles use only columns that exist in their target tables. No critical/major issues found. Three minor cosmetic/coverage gaps are documented below.

### Findings counts

| Severity | Count |
|---|---|
| Critical | **0** |
| Major    | **0** |
| Minor    | **3** |
| Notes    | **4** |

---

## 2. Critical findings

*(none)*

---

## 3. Major findings

*(none)*

---

## 4. Minor findings

### MN-01 — `dim_UserRoles` not exposed to the PowerBI model

**Where**: `powerbi/model/tables/` (no `dim_UserRoles.tmdl`).
**Spec**: `02_database_design.md` §3.10 defines `dim_UserRoles` as a first-class dimension; CLAUDE.md naming convention enforces the `dim_*` prefix on all dim tables; `roles.tmdl` header comment cites it as the source for SQL-side RLS scope assignment.
**Observed**: The TMDL model exposes 9 dim tables (`dim_Date`, `dim_Companies`, `dim_TradingFloors`, `dim_Teams`, `dim_Employees`, `dim_Accounts`, `dim_Markets`, `dim_Sessions`, `dim_OrderType`); `dim_UserRoles` is omitted.
**Impact**: PowerBI RLS roles (`FloorManager`, `TeamLead`, `Trader`) currently resolve user identity by joining `USERPRINCIPALNAME()` directly against `dim_Employees[email]`. This works for v1.0 because the project assumes `email = UPN`, but it diverges from the SQL-side RLS (ADR-003) which goes through `dim_UserRoles.aad_object_id`. If a service account or a delegated principal needs to be granted floor-scope without an `email` row, the model has no path to express that today.
**Recommendation**: Either (a) accept the divergence and document it in `roles.tmdl` (the existing header note partially does this), or (b) add a hidden `dim_UserRoles.tmdl` mapping `aad_object_id`, `employee_id`, `scope`, `is_active` and refactor the role expressions to LOOKUPVALUE through it. v1.0 ships with option (a); the architectural cost of the gap is bounded.

### MN-02 — `fact_Trades` not exposed; `dim_Accounts` is orphaned

**Where**: `powerbi/model/tables/dim_Accounts.tmdl`, `relationships.tmdl`.
**Spec**: `02_database_design.md` §4.1 (`fact_Trades`) and §3.5 (`dim_Accounts.trader_id`).
**Observed**:
1. There is no `fact_Trades` table in the TMDL model. The model exposes `v_trades_enriched` (a SELECT * over `fact_Trades` joined to its dims) as the row-grain analytical artifact. This is an intentional design decision — analysts shouldn't see the FK-only fact alone — and `01_business_requirements.md` does not require direct `fact_Trades` exposure. **Not a defect**, but worth flagging because PowerBI patterns commonly expect a `fact_*` table.
2. `dim_Accounts` is loaded but has **zero relationships** in `relationships.tmdl`. It is currently dead weight in the model (no measure references it; no FK column on any view goes to `account_id`).
**Impact**: `dim_Accounts` adds memory/refresh time with no payoff. PowerBI Field List shows an unconnectable dimension which can confuse end users.
**Recommendation**: Either (a) drop `dim_Accounts.tmdl` from the v1.0 model (it can be reintroduced when an `account_id` column lands on a view), or (b) add `account_id` to `v_trades_enriched` and wire the relationship. Lowest-risk: option (a). The dim is preserved in the DB layer for future use.

### MN-03 — `dim_Employees.aad_object_id` not exposed to the TMDL model

**Where**: `powerbi/model/tables/dim_Employees.tmdl`.
**Spec**: `02_database_design.md` §3.4 declares `aad_object_id UNIQUEIDENTIFIER` as the AAD-RLS identity field; `roles.tmdl` resolves identity via `email` instead.
**Observed**: `aad_object_id` exists in `dim_Employees` but is **not declared** in the TMDL column list.
**Impact**: If RLS is hardened to AAD-OID-based lookup in Etapa-12, the column will need to be added before the role expressions can switch from `[email]` to `[aad_object_id]`. Today RLS works because the project explicitly aligns `email = UPN` (see roles.tmdl §1 comment).
**Recommendation**: Optionally add a hidden `aad_object_id` column to `dim_Employees.tmdl` to future-proof. Non-blocking for v1.0.

---

## 5. Notes (informational, not findings)

### N-01 — `_Measures` placeholder column conforms to TMDL best practice
The `_Measures` table has a hidden `placeholder` (int64) column and a `calculated` partition with source `{0}`. This is the canonical pattern for centralised-measure tables. Documented inline. ✅

### N-02 — `discourageImplicitMeasures` correctly set at the database level
`database.tmdl` line 6 declares `discourageImplicitMeasures` (no value, boolean flag). All summarisable fact columns set `summarizeBy: sum` (e.g., `net_pnl_eur_total`) so visuals still have implicit defaults; the flag merely warns analysts to prefer the named KPIs. ✅

### N-03 — All M expressions are parameterised
Every one of the 10 partition `source` expressions reads:
```m
Sql.Database(SqlServer, SqlDatabase),
Source{[Schema="dbo", Item="<view_or_dim_name>"]}[Data]
```
The `SqlServer` and `SqlDatabase` model expressions in `model.tmdl` are flagged `IsParameterQuery=true, IsParameterQueryRequired=true` with documented substitution at deploy time via `powerbi/deploy.ps1`. Schema is hardcoded to `"dbo"` (the only schema used by V001). No hardcoded server names found. ✅

### N-04 — Star-schema integrity
The five view tables (`v_trades_enriched`, `v_employee_performance`, `v_team_performance`, `v_floor_performance`, `v_daily_pnl`) all join to `dim_Date` via `trade_date_ro → calendar_date` (single-direction, active). The hierarchy `dim_Employees → dim_Teams → dim_TradingFloors → dim_Companies` is declared. Dimension joins from `v_trades_enriched` to `dim_Markets`, `dim_Sessions`, `dim_OrderType` are present. All 16 relationships use `crossFilteringBehavior: oneDirection` per Vertipaq best practice. ✅

---

## 6. Table-by-table column conformance matrix

Legend: ✓ = TMDL column maps to an existing source column with a compatible datatype. ✗ = mismatch. — = column absent from TMDL but present in DB (noted in §4 if material).

### 6.1 `dim_Companies` (7 columns)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| company_id      | int64    | INT IDENTITY        | ✓ |
| legal_name      | string   | NVARCHAR(200)       | ✓ |
| short_name      | string   | NVARCHAR(50)        | ✓ |
| country_code    | string   | CHAR(2)             | ✓ |
| base_currency   | string   | CHAR(3)             | ✓ |
| created_at      | dateTime | DATETIMEOFFSET(3)   | ✓ |
| updated_at      | dateTime | DATETIMEOFFSET(3)   | ✓ |

### 6.2 `dim_TradingFloors` (8 columns)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| floor_id        | int64    | INT IDENTITY        | ✓ |
| company_id      | int64    | INT                 | ✓ |
| city            | string   | NVARCHAR(100)       | ✓ |
| floor_code      | string   | VARCHAR(10)         | ✓ |
| is_primary_hq   | boolean  | BIT                 | ✓ |
| opened_on       | dateTime | DATE                | ✓ (nullable matches) |
| created_at      | dateTime | DATETIMEOFFSET(3)   | ✓ |
| updated_at      | dateTime | DATETIMEOFFSET(3)   | ✓ |

### 6.3 `dim_Teams` (6 columns)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| team_id     | int64    | INT IDENTITY        | ✓ |
| floor_id    | int64    | INT                 | ✓ |
| team_name   | string   | NVARCHAR(100)       | ✓ |
| team_code   | string   | VARCHAR(20)         | ✓ |
| created_at  | dateTime | DATETIMEOFFSET(3)   | ✓ |
| updated_at  | dateTime | DATETIMEOFFSET(3)   | ✓ |

### 6.4 `dim_Employees` (13 columns declared; 1 omitted)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| employee_id          | int64    | INT IDENTITY      | ✓ |
| company_id           | int64    | INT               | ✓ (hidden) |
| floor_id             | int64    | INT               | ✓ |
| team_id              | int64    | INT               | ✓ |
| manager_employee_id  | int64    | INT NULL          | ✓ (hidden, nullable) |
| first_name           | string   | NVARCHAR(80)      | ✓ |
| last_name            | string   | NVARCHAR(80)      | ✓ |
| email                | string   | NVARCHAR(254)     | ✓ |
| employee_role        | string   | VARCHAR(20)       | ✓ |
| hire_date            | dateTime | DATE              | ✓ |
| is_active            | boolean  | BIT               | ✓ |
| created_at           | dateTime | DATETIMEOFFSET(3) | ✓ |
| updated_at           | dateTime | DATETIMEOFFSET(3) | ✓ |
| *(omitted)* aad_object_id | —        | UNIQUEIDENTIFIER  | — (see MN-03) |

### 6.5 `dim_Accounts` (9 columns)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| account_id   | int64    | INT IDENTITY      | ✓ |
| trader_id    | int64    | INT               | ✓ |
| account_code | string   | VARCHAR(30)       | ✓ |
| account_type | string   | VARCHAR(20)       | ✓ |
| currency     | string   | CHAR(3)           | ✓ |
| opened_on    | dateTime | DATE              | ✓ |
| is_active    | boolean  | BIT               | ✓ |
| created_at   | dateTime | DATETIMEOFFSET(3) | ✓ |
| updated_at   | dateTime | DATETIMEOFFSET(3) | ✓ |

*(Table itself is unreferenced — see MN-02.)*

### 6.6 `dim_Markets` (9 columns)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| market_id      | int64    | INT IDENTITY      | ✓ |
| symbol         | string   | VARCHAR(20)       | ✓ |
| display_name   | string   | NVARCHAR(100)     | ✓ |
| asset_class    | string   | VARCHAR(20)       | ✓ |
| quote_currency | string   | CHAR(3)           | ✓ |
| tick_size      | decimal  | DECIMAL(18,8)     | ✓ |
| is_active      | boolean  | BIT               | ✓ |
| created_at     | dateTime | DATETIMEOFFSET(3) | ✓ |
| updated_at     | dateTime | DATETIMEOFFSET(3) | ✓ |

### 6.7 `dim_Sessions` (5 columns)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| session_id        | int64    | INT IDENTITY  | ✓ |
| session_code      | string   | VARCHAR(20)   | ✓ |
| display_name      | string   | NVARCHAR(50)  | ✓ |
| start_time_local  | dateTime | TIME(0)       | ✓ (PowerBI promotes TIME to dateTime/duration; format `HH:mm` enforced) |
| end_time_local    | dateTime | TIME(0)       | ✓ |

*(Note: TMDL omits `created_at` from `dim_Sessions`. The column is unused for analysis. Acceptable.)*

### 6.8 `dim_OrderType` (4 columns)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| order_type_id   | int64    | INT IDENTITY | ✓ |
| order_type_code | string   | VARCHAR(20)  | ✓ |
| display_name    | string   | NVARCHAR(50) | ✓ |
| is_directional  | boolean  | BIT          | ✓ |

*(Note: TMDL omits `created_at`. Acceptable.)*

### 6.9 `dim_Date` (16 columns)

| TMDL column | TMDL type | V001 type | Conformant? |
|---|---|---|---|
| date_id          | int64    | INT          | ✓ (hidden) |
| calendar_date    | dateTime | DATE         | ✓ (isKey) |
| iso_year         | int64    | INT          | ✓ |
| iso_week         | int64    | INT          | ✓ |
| year             | int64    | INT          | ✓ |
| quarter          | int64    | TINYINT      | ✓ (int64 ⊇ TINYINT) |
| month            | int64    | TINYINT      | ✓ |
| month_name_ro    | string   | NVARCHAR(20) | ✓ |
| month_name_en    | string   | NVARCHAR(20) | ✓ |
| day_of_month     | int64    | TINYINT      | ✓ |
| day_of_week      | int64    | TINYINT      | ✓ |
| is_weekday       | boolean  | BIT          | ✓ |
| is_ro_holiday    | boolean  | BIT          | ✓ |
| ro_holiday_name  | string   | NVARCHAR(80) NULL | ✓ |
| en_holiday_name  | string   | NVARCHAR(80) NULL | ✓ |
| fiscal_year      | int64    | computed INT PERSISTED | ✓ |

`dataCategory: time` correctly declared; `Calendar` hierarchy (Year/Quarter/Month/ISOWeek/Day) is built. ✅

### 6.10 `v_trades_enriched` (25 columns)

| TMDL column | TMDL type | View output type | Conformant? |
|---|---|---|---|
| trade_uid          | string   | VARCHAR(14)       | ✓ |
| trade_date_ro      | dateTime | DATE              | ✓ |
| time_entry         | dateTime | DATETIMEOFFSET(3) | ✓ |
| time_exit          | dateTime | DATETIMEOFFSET(3) NULL | ✓ |
| trader_id          | int64    | INT               | ✓ |
| trader_full_name   | string   | NVARCHAR (concat) | ✓ |
| team_id            | int64    | INT               | ✓ |
| team_name          | string   | NVARCHAR(100)     | ✓ |
| floor_id           | int64    | INT               | ✓ |
| floor_city         | string   | NVARCHAR(100)     | ✓ |
| market_id          | int64    | INT               | ✓ |
| symbol             | string   | VARCHAR(20)       | ✓ |
| asset_class        | string   | VARCHAR(20)       | ✓ |
| session_code       | string   | VARCHAR(20)       | ✓ |
| order_type_code    | string   | VARCHAR(20)       | ✓ |
| side               | string   | CHAR(1)           | ✓ |
| quantity           | decimal  | DECIMAL(18,4)     | ✓ |
| price_entry        | decimal  | DECIMAL(18,6)     | ✓ |
| price_exit         | decimal  | DECIMAL(18,6) NULL| ✓ |
| gross_pnl_eur      | decimal  | DECIMAL(18,4) NULL| ✓ |
| commission_eur     | decimal  | DECIMAL(18,4)     | ✓ |
| net_pnl_eur        | decimal  | DECIMAL(18,4) NULL| ✓ |
| is_open            | boolean  | BIT               | ✓ |
| holding_time_minutes | decimal | DATEDIFF→INT NULL | ✓ (PowerBI widens to decimal cleanly) |
| pnl_per_unit       | decimal  | DECIMAL division NULL | ✓ |

### 6.11 `v_employee_performance` (13 columns)

| TMDL column | TMDL type | View output type | Conformant? |
|---|---|---|---|
| trade_date_ro            | dateTime | DATE              | ✓ |
| employee_id              | int64    | INT               | ✓ |
| trader_full_name         | string   | NVARCHAR          | ✓ |
| team_id                  | int64    | INT               | ✓ |
| floor_id                 | int64    | INT               | ✓ |
| trade_count              | int64    | BIGINT (COUNT_BIG)| ✓ |
| win_count                | int64    | INT               | ✓ |
| loss_count               | int64    | INT               | ✓ |
| win_rate                 | decimal  | DECIMAL(9,6)      | ✓ |
| gross_pnl_eur_total      | decimal  | DECIMAL(18,4)     | ✓ |
| commission_eur_total     | decimal  | DECIMAL(18,4)     | ✓ |
| net_pnl_eur_total        | decimal  | DECIMAL(18,4)     | ✓ |
| avg_holding_time_minutes | decimal  | DECIMAL(18,4) NULL| ✓ |

### 6.12 `v_team_performance` (12 columns)

| TMDL column | TMDL type | View output type | Conformant? |
|---|---|---|---|
| trade_date_ro            | dateTime | DATE              | ✓ |
| team_id                  | int64    | INT               | ✓ |
| team_name                | string   | NVARCHAR(100)     | ✓ |
| floor_id                 | int64    | INT               | ✓ |
| trade_count              | int64    | BIGINT            | ✓ |
| win_count                | int64    | INT               | ✓ |
| loss_count               | int64    | INT               | ✓ |
| win_rate                 | decimal  | DECIMAL(9,6)      | ✓ |
| gross_pnl_eur_total      | decimal  | DECIMAL(18,4)     | ✓ |
| commission_eur_total     | decimal  | DECIMAL(18,4)     | ✓ |
| net_pnl_eur_total        | decimal  | DECIMAL(18,4)     | ✓ |
| avg_holding_time_minutes | decimal  | DECIMAL(18,4) NULL| ✓ |

### 6.13 `v_floor_performance` (11 columns)

| TMDL column | TMDL type | View output type | Conformant? |
|---|---|---|---|
| trade_date_ro            | dateTime | DATE              | ✓ |
| floor_id                 | int64    | INT               | ✓ |
| floor_city               | string   | NVARCHAR(100)     | ✓ |
| trade_count              | int64    | BIGINT            | ✓ |
| win_count                | int64    | INT               | ✓ |
| loss_count               | int64    | INT               | ✓ |
| win_rate                 | decimal  | DECIMAL(9,6)      | ✓ |
| gross_pnl_eur_total      | decimal  | DECIMAL(18,4)     | ✓ |
| commission_eur_total     | decimal  | DECIMAL(18,4)     | ✓ |
| net_pnl_eur_total        | decimal  | DECIMAL(18,4)     | ✓ |
| avg_holding_time_minutes | decimal  | DECIMAL(18,4) NULL| ✓ |

### 6.14 `v_daily_pnl` (8 columns)

| TMDL column | TMDL type | View output type | Conformant? |
|---|---|---|---|
| employee_id            | int64    | INT             | ✓ |
| trader_full_name       | string   | NVARCHAR        | ✓ |
| team_id                | int64    | INT             | ✓ |
| floor_id               | int64    | INT             | ✓ |
| trade_date_ro          | dateTime | DATE            | ✓ |
| trade_count            | int64    | BIGINT          | ✓ |
| net_pnl_eur_total      | decimal  | DECIMAL(18,4)   | ✓ |
| cumulative_net_pnl_eur | decimal  | DECIMAL(18,4)   | ✓ |

---

## 7. Audit checklist verdicts

| Audit item | Verdict | Evidence |
|---|---|---|
| 1. Every TMDL table corresponds to a real view/dim | PASS (with note) | 14 TMDL tables; 14 dbo objects in V001. `_Measures` is a calculated table (no source). `dim_UserRoles` exists in DB but not in model (MN-01). |
| 2. Every column matches its source column with compatible datatype | PASS | Section 6 matrix shows full conformance. No type mismatches found. |
| 3. M expressions reference `SqlServer` + `SqlDatabase` parameters | PASS | All 10 partitions use `Sql.Database(SqlServer, SqlDatabase)`. Parameters declared in `model.tmdl` with `IsParameterQuery=true`. |
| 4. Every relationship has a corresponding FK or implicit join key | PASS | All 16 relationships map to FKs declared in `db/migrations/V001__init.sql` (`fact_Trades.trader_id`, `dim_Teams.floor_id`, etc.) or to the `trade_date_ro → calendar_date` join key (declared FK on `fact_Trades`, inherited by views). |
| 5. Star schema: `dim_Date.calendar_date` is canonical date axis | PASS | All 5 view tables connect to `dim_Date.calendar_date` via `trade_date_ro`. `dim_Date` flagged `dataCategory: time` with a built `Calendar` hierarchy. |
| 6. Import mode on every table partition | PASS | All 10 partitions declare `mode: import`. Database-level `defaultMode: import` set in `database.tmdl`. Matches ADR-001 mandate. |
| 7. RLS roles filter by columns that exist | PASS | `Admin` (no filter), `FloorManager` (dim_TradingFloors.floor_id, dim_Employees.floor_id+email), `TeamLead` (dim_Teams.team_id, dim_Employees.team_id+email), `Trader` (dim_Employees.email + employee_id; v_employee_performance.employee_id; v_daily_pnl.employee_id; v_trades_enriched.trader_id). All referenced columns exist. |
| 8. `_Measures` table has hidden placeholder column + docs | PASS | `placeholder` (int64, isHidden); `calculated` partition with `source = {0}`; doc comment §1 line 3 documents rationale. |
| 9. `discourageImplicitMeasures` set at model level | PASS | `database.tmdl` line 6. |
| 10. Schema names in M expressions: `Schema="dbo"` | PASS | All 10 partitions use `Schema="dbo"`. |

---

## 8. Cross-cutting observations

### 8.1 Naming convention compliance
All TMDL table names follow the project naming convention (`dim_PascalName`, `fact_PascalName`, `config_PascalName`, `v_snake_case_for_views`). The `_Measures` table uses the leading-underscore convention common in tabular models to sort it first alphabetically in the Field List; this is consistent with TMDL best practice and does not conflict with CLAUDE.md (CLAUDE.md's regex applies to DB tables, not PowerBI measure-host tables).

### 8.2 Localisation
`ro-RO` culture file translates all 48 measures and 9 table captions to Romanian, matching the dataset locale `ro-RO` set in `database.tmdl`. Column identifiers remain in English (correctly — DAX requires stable English identifiers for refactor safety). Decimal-separator/thousand-separator/date-format conventions are inherited from the culture metadata. ✅

### 8.3 Measure architecture (centralised `_Measures`)
48 measures defined; KPI codes from `01_business_requirements.md §4` cleanly map to measure names (e.g., `KPI-TR-010 Net PnL`). The choice to centralise measures in `_Measures` rather than co-locating each measure with its host table is documented inline. Some measures span tables (e.g., KPI-TR-053 `RELATED(dim_Date[is_weekday])` from `v_trades_enriched` requires the `v_trades_enriched_date` relationship, which exists ✅). No measure references a column that does not exist in its referenced table.

### 8.4 Deployment-time parameter substitution
The model's `SqlServer` and `SqlDatabase` parameters are placeholders. The deploy script (`powerbi/deploy.ps1`, scope of Etapa-7) is expected to substitute via the PowerBI REST `Default.UpdateDatasources` call per ADR-001. **Out of scope for this review** — flagged for the deploy-script-pass review.

### 8.5 RLS-vs-AAD identity binding
The Trader/TeamLead/FloorManager roles resolve identity by joining `USERPRINCIPALNAME()` against `dim_Employees[email]`. This works because the project explicitly aligns the corporate email domain (`@tcp-capital.ro`) to the AAD UPN. The `roles.tmdl` header documents this design choice (lines 1-10) and notes that the SQL-side RLS contract (`SESSION_CONTEXT`) lives separately in ADR-003. ✅

---

## 9. Recommendations summary

1. **MN-01**: Decide whether to mirror `dim_UserRoles` into the PowerBI model (option a: do nothing, document divergence; option b: add the dim). v1.0 ships option (a) safely.
2. **MN-02**: Either drop `dim_Accounts.tmdl` from v1.0 or expose `account_id` on `v_trades_enriched` and wire the relationship. Recommend drop until needed.
3. **MN-03**: Optionally add hidden `aad_object_id` column to `dim_Employees.tmdl` to future-proof Etapa-12 RLS hardening.

None of the above blocks Etapa-7 sign-off. The semantic model is approved for deployment.

---

## 10. References

- `powerbi/model/database.tmdl`, `model.tmdl`, `relationships.tmdl`, `roles.tmdl`
- `powerbi/model/tables/*.tmdl` (10 table files + `_Measures.tmdl`)
- `powerbi/model/cultures/ro-RO.tmdl`
- `docs/design/02_database_design.md` §§3, 4, 6
- `db/migrations/V001__init.sql`
- `docs/decisions/ADR-001-powerbi-deployment.md`
- `docs/decisions/ADR-003-rls-session-context-contract.md` (RLS contract, referenced by `roles.tmdl`)
