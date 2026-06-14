-- Migration: V001 (file-name-keyed in dbo.schema_history)

-- =====================================================================
-- TCP -- Trading Central Panel
-- Migration V001 -- initial schema
-- Source of truth: docs/design/02_database_design.md §15
-- ADRs incorporated: ADR-002 (fact_DailyTraderPnL), ADR-003 (RLS)
-- Apply with: sqlcmd -S <server> -d <db> -G -i V001__init.sql -b
--
-- Idempotent: every object is guarded so a re-apply on a partially
-- populated database is safe and produces the same end state.
-- =====================================================================

SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

-- ============ 0. SCHEMA HISTORY (migration ledger) ============
IF OBJECT_ID(N'dbo.schema_history', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.schema_history
    (
        script_name      NVARCHAR(200)     NOT NULL CONSTRAINT PK_schema_history PRIMARY KEY,
        applied_at_utc   DATETIME2(3)      NOT NULL,
        checksum         NVARCHAR(128)     NULL
    );
END;
GO

-- ============ 1. SCHEMAS ============
IF SCHEMA_ID(N'rls') IS NULL
    EXEC(N'CREATE SCHEMA rls AUTHORIZATION dbo;');
GO

-- ============ 2. CUSTOM DATABASE ROLES ============
IF DATABASE_PRINCIPAL_ID(N'tcp_ai_assistant') IS NULL
    CREATE ROLE tcp_ai_assistant AUTHORIZATION dbo;
IF DATABASE_PRINCIPAL_ID(N'tcp_generator') IS NULL
    CREATE ROLE tcp_generator    AUTHORIZATION dbo;
IF DATABASE_PRINCIPAL_ID(N'tcp_bi_reader') IS NULL
    CREATE ROLE tcp_bi_reader    AUTHORIZATION dbo;
IF DATABASE_PRINCIPAL_ID(N'tcp_admin') IS NULL
    CREATE ROLE tcp_admin        AUTHORIZATION dbo;
GO

-- ============ 3. DIMENSION TABLES ============

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
GO

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
GO

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
GO

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
GO

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
GO

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
GO

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
GO

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
GO

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
GO

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

    CREATE UNIQUE INDEX UX_dim_UserRoles_aad_object_id_active
        ON dbo.dim_UserRoles(aad_object_id)
        WHERE is_active = 1;

    CREATE INDEX IX_dim_UserRoles_aad_object_id_INC
        ON dbo.dim_UserRoles(aad_object_id, is_active)
        INCLUDE (employee_id, scope);
END;
GO

-- ============ 4. CONFIG TABLES ============
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
GO

-- ============ 5. FACT TABLES ============

-- fact_Trades
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
        -- `CAST(datetimeoffset AS date)` returns the LOCAL date of the offset
        -- without consulting the Windows timezone DB, so SQL Server marks it
        -- deterministic and allows PERSISTED (required for FK + index).
        -- `time_entry` is documented to carry the Europe/Bucharest offset
        -- (CLAUDE.md "Timestamps and locale"), so the local date IS the RO
        -- trade date. The earlier `AT TIME ZONE 'E. Europe Standard Time'`
        -- form was rejected on apply with SQL Msg 4936 (non-deterministic).
        trade_date_ro   AS CAST(time_entry AS DATE) PERSISTED NOT NULL,
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
GO

-- fact_DailyTraderPnL (per ADR-002)
IF OBJECT_ID(N'dbo.fact_DailyTraderPnL', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.fact_DailyTraderPnL
    (
        daily_pnl_id          INT IDENTITY(1,1) NOT NULL,
        employee_id           INT               NOT NULL,
        trade_date_ro         DATE              NOT NULL,
        trade_count           INT               NOT NULL,
        gross_pnl_eur_total   DECIMAL(18,4)     NOT NULL,
        net_pnl_eur_total     DECIMAL(18,4)     NOT NULL,
        commission_eur_total  DECIMAL(18,4)     NOT NULL,
        win_count             INT               NOT NULL,
        loss_count            INT               NOT NULL,
        avg_holding_minutes   DECIMAL(18,4)     NULL,
        created_at            DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_fact_DailyTraderPnL_created_at DEFAULT (SYSDATETIMEOFFSET()),
        updated_at            DATETIMEOFFSET(3) NOT NULL CONSTRAINT DF_fact_DailyTraderPnL_updated_at DEFAULT (SYSDATETIMEOFFSET()),
        CONSTRAINT PK_fact_DailyTraderPnL PRIMARY KEY (employee_id, trade_date_ro),
        CONSTRAINT UQ_fact_DailyTraderPnL_daily_pnl_id UNIQUE NONCLUSTERED (daily_pnl_id),
        CONSTRAINT FK_fact_DailyTraderPnL_dim_Employees
            FOREIGN KEY (employee_id)   REFERENCES dbo.dim_Employees(employee_id),
        CONSTRAINT FK_fact_DailyTraderPnL_dim_Date
            FOREIGN KEY (trade_date_ro) REFERENCES dbo.dim_Date(calendar_date),
        CONSTRAINT CK_fact_DailyTraderPnL_trade_count CHECK (trade_count >= 0),
        CONSTRAINT CK_fact_DailyTraderPnL_counts      CHECK (win_count  >= 0 AND loss_count >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_fact_DailyTraderPnL_trade_date_ro
        ON dbo.fact_DailyTraderPnL(trade_date_ro)
        INCLUDE (net_pnl_eur_total);
END;
GO

-- ============ 6. SEED DATA (idempotent MERGE upserts) ============

-- dim_Companies (single tenant row)
IF NOT EXISTS (SELECT 1 FROM dbo.dim_Companies WHERE legal_name = N'TCP Capital Management SRL')
    INSERT INTO dbo.dim_Companies (legal_name, short_name, country_code, base_currency)
    VALUES (N'TCP Capital Management SRL', N'TCP', 'RO', 'EUR');
GO

-- dim_TradingFloors
MERGE dbo.dim_TradingFloors AS tgt
USING (VALUES
    (1, N'București',   'BUC', CAST(1 AS BIT), CAST('2018-01-01' AS DATE)),
    (1, N'Cluj-Napoca', 'CLJ', CAST(0 AS BIT), CAST('2021-06-01' AS DATE))
) AS src (company_id, city, floor_code, is_primary_hq, opened_on)
   ON tgt.floor_code = src.floor_code
WHEN MATCHED THEN UPDATE SET
    company_id    = src.company_id,
    city          = src.city,
    is_primary_hq = src.is_primary_hq,
    opened_on     = src.opened_on,
    updated_at    = SYSDATETIMEOFFSET()
WHEN NOT MATCHED BY TARGET THEN
    INSERT (company_id, city, floor_code, is_primary_hq, opened_on)
    VALUES (src.company_id, src.city, src.floor_code, src.is_primary_hq, src.opened_on);
GO

-- dim_Teams
MERGE dbo.dim_Teams AS tgt
USING (VALUES
    (1, N'Alpha',   'BUC-A'),
    (1, N'Bravo',   'BUC-B'),
    (1, N'Charlie', 'BUC-C'),
    (2, N'Delta',   'CLJ-D'),
    (2, N'Echo',    'CLJ-E'),
    (2, N'Foxtrot', 'CLJ-F')
) AS src (floor_id, team_name, team_code)
   ON tgt.team_code = src.team_code
WHEN MATCHED THEN UPDATE SET
    floor_id   = src.floor_id,
    team_name  = src.team_name,
    updated_at = SYSDATETIMEOFFSET()
WHEN NOT MATCHED BY TARGET THEN
    INSERT (floor_id, team_name, team_code)
    VALUES (src.floor_id, src.team_name, src.team_code);
GO

-- dim_Sessions
MERGE dbo.dim_Sessions AS tgt
USING (VALUES
    ('pre_market',  N'Pre-market',  CAST('07:00' AS TIME(0)), CAST('09:30' AS TIME(0))),
    ('regular',     N'Regular',     CAST('09:30' AS TIME(0)), CAST('17:30' AS TIME(0))),
    ('after_hours', N'After-hours', CAST('17:30' AS TIME(0)), CAST('22:00' AS TIME(0)))
) AS src (session_code, display_name, start_time_local, end_time_local)
   ON tgt.session_code = src.session_code
WHEN MATCHED THEN UPDATE SET
    display_name     = src.display_name,
    start_time_local = src.start_time_local,
    end_time_local   = src.end_time_local
WHEN NOT MATCHED BY TARGET THEN
    INSERT (session_code, display_name, start_time_local, end_time_local)
    VALUES (src.session_code, src.display_name, src.start_time_local, src.end_time_local);
GO

-- dim_OrderType
MERGE dbo.dim_OrderType AS tgt
USING (VALUES
    ('market',     N'Market',     CAST(1 AS BIT)),
    ('limit',      N'Limit',      CAST(1 AS BIT)),
    ('stop',       N'Stop',       CAST(1 AS BIT)),
    ('stop_limit', N'Stop-limit', CAST(1 AS BIT))
) AS src (order_type_code, display_name, is_directional)
   ON tgt.order_type_code = src.order_type_code
WHEN MATCHED THEN UPDATE SET
    display_name   = src.display_name,
    is_directional = src.is_directional
WHEN NOT MATCHED BY TARGET THEN
    INSERT (order_type_code, display_name, is_directional)
    VALUES (src.order_type_code, src.display_name, src.is_directional);
GO

-- dim_Markets
MERGE dbo.dim_Markets AS tgt
USING (VALUES
    ('AAPL',    N'Apple Inc.',                'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('MSFT',    N'Microsoft Corp.',           'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('GOOGL',   N'Alphabet Inc. Class A',     'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('AMZN',    N'Amazon.com Inc.',           'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('META',    N'Meta Platforms Inc.',       'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('TSLA',    N'Tesla Inc.',                'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('NVDA',    N'NVIDIA Corp.',              'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('JPM',     N'JPMorgan Chase & Co.',      'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('XOM',     N'Exxon Mobil Corp.',         'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('SPY',     N'SPDR S&P 500 ETF',          'equity',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('EURUSD',  N'Euro / US Dollar',          'fx',       'USD', CAST(0.00001   AS DECIMAL(18,8))),
    ('GBPUSD',  N'British Pound / USD',       'fx',       'USD', CAST(0.00001   AS DECIMAL(18,8))),
    ('USDJPY',  N'USD / Japanese Yen',        'fx',       'JPY', CAST(0.001     AS DECIMAL(18,8))),
    ('USDCHF',  N'USD / Swiss Franc',         'fx',       'CHF', CAST(0.00001   AS DECIMAL(18,8))),
    ('AUDUSD',  N'Australian Dollar / USD',   'fx',       'USD', CAST(0.00001   AS DECIMAL(18,8))),
    ('EURGBP',  N'Euro / British Pound',      'fx',       'GBP', CAST(0.00001   AS DECIMAL(18,8))),
    ('EURJPY',  N'Euro / Japanese Yen',       'fx',       'JPY', CAST(0.001     AS DECIMAL(18,8))),
    ('USDRON',  N'USD / Romanian Leu',        'fx',       'RON', CAST(0.0001    AS DECIMAL(18,8))),
    ('BTCUSD',  N'Bitcoin / USD',             'crypto',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('ETHUSD',  N'Ethereum / USD',            'crypto',   'USD', CAST(0.01      AS DECIMAL(18,8))),
    ('SOLUSD',  N'Solana / USD',              'crypto',   'USD', CAST(0.0001    AS DECIMAL(18,8))),
    ('ADAUSD',  N'Cardano / USD',             'crypto',   'USD', CAST(0.00001   AS DECIMAL(18,8))),
    ('XRPUSD',  N'XRP / USD',                 'crypto',   'USD', CAST(0.00001   AS DECIMAL(18,8))),
    ('DOGEUSD', N'Dogecoin / USD',            'crypto',   'USD', CAST(0.000001  AS DECIMAL(18,8))),
    ('XAUUSD',  N'Gold Spot / USD',           'commodity','USD', CAST(0.01      AS DECIMAL(18,8))),
    ('XAGUSD',  N'Silver Spot / USD',         'commodity','USD', CAST(0.001     AS DECIMAL(18,8))),
    ('WTI',     N'WTI Crude Oil',             'commodity','USD', CAST(0.01      AS DECIMAL(18,8))),
    ('BRENT',   N'Brent Crude Oil',           'commodity','USD', CAST(0.01      AS DECIMAL(18,8))),
    ('NATGAS',  N'Natural Gas',               'commodity','USD', CAST(0.001     AS DECIMAL(18,8))),
    ('COPPER',  N'High-Grade Copper',         'commodity','USD', CAST(0.0005    AS DECIMAL(18,8)))
) AS src (symbol, display_name, asset_class, quote_currency, tick_size)
   ON tgt.symbol = src.symbol
WHEN MATCHED THEN UPDATE SET
    display_name   = src.display_name,
    asset_class    = src.asset_class,
    quote_currency = src.quote_currency,
    tick_size      = src.tick_size,
    updated_at     = SYSDATETIMEOFFSET()
WHEN NOT MATCHED BY TARGET THEN
    INSERT (symbol, display_name, asset_class, quote_currency, tick_size)
    VALUES (src.symbol, src.display_name, src.asset_class, src.quote_currency, src.tick_size);
GO

-- config_Capital (single global baseline)
IF NOT EXISTS (SELECT 1 FROM dbo.config_Capital WHERE trader_id IS NULL)
    INSERT INTO dbo.config_Capital (trader_id, amount_eur, effective_from, effective_to, note)
    VALUES (NULL, 80000.00, '2024-01-01T00:00:00+02:00', NULL, N'Initial global baseline.');
GO

-- ============ 7. dim_Date population (2024-01-01 .. 2030-12-31) ============
IF NOT EXISTS (SELECT 1 FROM dbo.dim_Date)
BEGIN
    SET DATEFIRST 1;

    ;WITH n0 AS (SELECT 1 AS x UNION ALL SELECT 1 AS x),
         n1 AS (SELECT 1 AS x FROM n0 a, n0 b),
         n2 AS (SELECT 1 AS x FROM n1 a, n1 b),
         n3 AS (SELECT 1 AS x FROM n2 a, n2 b),
         n4 AS (SELECT 1 AS x FROM n3 a, n3 b),
         tally AS (
            SELECT TOP (DATEDIFF(DAY, '2024-01-01', '2030-12-31') + 1)
                ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS d_offset
            FROM n4
         )
    INSERT INTO dbo.dim_Date
        (date_id, calendar_date, iso_year, iso_week, [year], [quarter], [month],
         month_name_ro, month_name_en, day_of_month, day_of_week, is_weekday, is_ro_holiday,
         ro_holiday_name, en_holiday_name)
    SELECT
        CONVERT(INT, CONVERT(VARCHAR(8), DATEADD(DAY, t.d_offset, '2024-01-01'), 112)) AS date_id,
        DATEADD(DAY, t.d_offset, '2024-01-01')                                          AS calendar_date,
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
        DATEPART(WEEKDAY,  DATEADD(DAY, t.d_offset, '2024-01-01'))                       AS day_of_week,
        CASE WHEN DATEPART(WEEKDAY, DATEADD(DAY, t.d_offset, '2024-01-01')) BETWEEN 1 AND 5
             THEN 1 ELSE 0 END                                                            AS is_weekday,
        0                                                                                  AS is_ro_holiday,
        NULL                                                                               AS ro_holiday_name,
        NULL                                                                               AS en_holiday_name
    FROM tally AS t;

    -- Romanian public holidays 2024-2030 (Codul Muncii art. 139).
    -- Easter Sunday (Orthodox): 2024-05-05, 2025-04-20, 2026-04-12, 2027-05-02,
    -- 2028-04-16, 2029-04-08, 2030-04-28. Good Friday = Sunday - 2;
    -- Easter Monday = Sunday + 1; Pentecost Monday = Sunday + 50.
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
        ('2024-05-06', N'A doua zi de Paște',           N'Orthodox Easter Monday'),
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
        ('2025-04-21', N'A doua zi de Paște',           N'Orthodox Easter Monday'),
        ('2025-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2025-06-01', N'Ziua Copilului',               N'Children''s Day'),
        ('2025-06-08', N'Rusalii',                      N'Pentecost'),
        ('2025-06-09', N'Rusalii',                      N'Pentecost Monday'),
        ('2025-08-15', N'Adormirea Maicii Domnului',    N'Dormition of the Mother of God'),
        ('2025-11-30', N'Sfântul Andrei',               N'Saint Andrew'),
        ('2025-12-01', N'Ziua Națională a României',    N'Romania National Day'),
        ('2025-12-25', N'Crăciunul',                    N'Christmas Day'),
        ('2025-12-26', N'Crăciunul',                    N'Christmas Day (day 2)'),
        -- 2026 -- Pentecost Monday (June 1) collides with Children's Day; combined label.
        ('2026-01-01', N'Anul Nou',                     N'New Year''s Day'),
        ('2026-01-02', N'Anul Nou',                     N'New Year''s Day (day 2)'),
        ('2026-01-06', N'Bobotează',                    N'Epiphany'),
        ('2026-01-07', N'Sfântul Ion',                  N'Saint John the Baptist'),
        ('2026-01-24', N'Unirea Principatelor Române',  N'Union of the Romanian Principalities'),
        ('2026-04-10', N'Vinerea Mare',                 N'Good Friday'),
        ('2026-04-12', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2026-04-13', N'A doua zi de Paște',           N'Orthodox Easter Monday'),
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
        ('2027-05-01', N'Ziua Muncii',                  N'Labour Day'),
        ('2027-05-02', N'Paștele',                      N'Orthodox Easter Sunday'),
        ('2027-05-03', N'A doua zi de Paște',           N'Orthodox Easter Monday'),
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
        ('2028-04-17', N'A doua zi de Paște',           N'Orthodox Easter Monday'),
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
        ('2029-04-09', N'A doua zi de Paște',           N'Orthodox Easter Monday'),
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
        ('2030-04-29', N'A doua zi de Paște',           N'Orthodox Easter Monday'),
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

-- ============ 8. FUNCTIONS ============

--/ Inline TVF variant of fn_GetCapitalBaseline (optimizer-friendly).
--/ Defined BEFORE the scalar wrapper because both use `WITH SCHEMABINDING`,
--/ which forces CREATE-time reference checking (no schema-deferred resolution).
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

--/ Resolve the effective EUR capital baseline for a trader at @as_of (per-trader row preferred over global row).
--/ Thin scalar wrapper over dbo.tvf_GetCapitalBaseline; keep both functions present (some call sites need scalar form).
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
    SELECT @amount = amount_eur
    FROM dbo.tvf_GetCapitalBaseline(@trader_id, @as_of);
    RETURN @amount;
END;
GO

--/ Return 1 when @d is a Romanian weekday business day (non-weekend, non-holiday), else 0.
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

--/ Return the most recent Romanian business day strictly before @d.
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

-- ============ 9. VIEWS ============

--/ Enriched fact_Trades flattening: joins dims for trader/team/floor/market/session/order-type.
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
JOIN dbo.dim_Employees          AS e  ON e.employee_id   = f.trader_id
JOIN dbo.dim_Teams              AS t  ON t.team_id       = e.team_id
JOIN dbo.dim_TradingFloors      AS tf ON tf.floor_id     = e.floor_id
JOIN dbo.dim_Markets            AS m  ON m.market_id     = f.market_id
JOIN dbo.dim_Sessions           AS s  ON s.session_id    = f.session_id
JOIN dbo.dim_OrderType          AS o  ON o.order_type_id = f.order_type_id;
GO

--/ Per-(date, trader) aggregation of closed trades with win/loss/PnL totals.
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

--/ Per-(date, team) aggregation of closed trades.
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

--/ Per-(date, floor) aggregation of closed trades.
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

--/ Daily PnL series with cumulative running total per employee (presentation layer).
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

--/ Inline TVF: risk metrics (mean/stdev/downside-stdev/VaR-95/total PnL) per employee over a date range.
--/ Percentile is computed in a separate derived table joined via CROSS APPLY so the scalar
--/ aggregates and the PERCENTILE_CONT window function can coexist in a single-row output
--/ without a GROUP BY (Msg 8120 trap when mixed in one SELECT).
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
        a.trading_days,
        a.mean_daily_pnl,
        a.stdev_daily_pnl,
        a.stdev_downside,
        p.var_95,
        a.total_net_pnl
    FROM (
        SELECT
            COUNT_BIG(*)                                                            AS trading_days,
            AVG(ep.net_pnl_eur_total)                                               AS mean_daily_pnl,
            STDEV(ep.net_pnl_eur_total)                                             AS stdev_daily_pnl,
            STDEV(CASE WHEN ep.net_pnl_eur_total < 0 THEN ep.net_pnl_eur_total END) AS stdev_downside,
            SUM(ep.net_pnl_eur_total)                                               AS total_net_pnl
        FROM dbo.v_employee_performance AS ep
        WHERE ep.employee_id    = @employee_id
          AND ep.trade_date_ro BETWEEN @from AND @to
    ) AS a
    CROSS APPLY (
        SELECT DISTINCT PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY ep.net_pnl_eur_total) OVER () AS var_95
        FROM dbo.v_employee_performance AS ep
        WHERE ep.employee_id    = @employee_id
          AND ep.trade_date_ro BETWEEN @from AND @to
    ) AS p;
GO

-- ============ 10. STORED PROCEDURES ============

--/ Daily trade generator entry point. V001 ships the parameter-validation + idempotency
--/ scaffolding only; the synthesis body lands in V002__synth_tvp.sql (Etapa 5).
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
            SELECT CAST(0 AS INT) AS rows_inserted, CAST('already_generated' AS NVARCHAR(30)) AS [status];
            RETURN 0;
        END;

        DECLARE @inserted INT = 0;

        COMMIT;

        SELECT @inserted AS rows_inserted, CAST('inserted' AS NVARCHAR(30)) AS [status];
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK;
        THROW;
    END CATCH
END;
GO

--/ Return per-day performance rows for one employee within [@from, @to].
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

--/ Return the top N performers for the given scope (trader/team/floor) over [@from, @to].
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

-- ============ 11. ROW-LEVEL SECURITY ============

--/ RLS predicate -- resolves caller via SESSION_CONTEXT('aad_object_id') -> dim_UserRoles -> scope.
CREATE OR ALTER FUNCTION rls.fn_TradesPredicate(@trader_id_in_row INT)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    SELECT 1 AS result
    FROM (
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
    FROM sys.security_policies sp
    JOIN sys.schemas s ON s.schema_id = sp.schema_id
    WHERE sp.name = N'TradesAccessPolicy'
      AND s.name  = N'rls'
)
BEGIN
    EXEC(N'
        CREATE SECURITY POLICY rls.TradesAccessPolicy
        ADD FILTER PREDICATE rls.fn_TradesPredicate(trader_id)   ON dbo.fact_Trades,
        ADD BLOCK  PREDICATE rls.fn_TradesPredicate(trader_id)   ON dbo.fact_Trades AFTER INSERT,
        ADD BLOCK  PREDICATE rls.fn_TradesPredicate(trader_id)   ON dbo.fact_Trades AFTER UPDATE,
        ADD FILTER PREDICATE rls.fn_TradesPredicate(employee_id) ON dbo.fact_DailyTraderPnL,
        ADD BLOCK  PREDICATE rls.fn_TradesPredicate(employee_id) ON dbo.fact_DailyTraderPnL AFTER INSERT,
        ADD BLOCK  PREDICATE rls.fn_TradesPredicate(employee_id) ON dbo.fact_DailyTraderPnL AFTER UPDATE
        WITH (STATE = ON);
    ');
END;
GO

-- ============ 12. GRANTS (per docs/design/02_database_design.md §10.2) ============

-- tcp_ai_assistant: read views + execute read-only sprocs + read RLS-predicate tables.
GRANT EXECUTE ON dbo.usp_GetEmployeePerformance TO tcp_ai_assistant;
GRANT EXECUTE ON dbo.usp_GetTopPerformers       TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_trades_enriched          TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_employee_performance     TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_team_performance         TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_floor_performance        TO tcp_ai_assistant;
GRANT SELECT  ON dbo.v_daily_pnl                TO tcp_ai_assistant;
GRANT SELECT  ON dbo.fact_DailyTraderPnL        TO tcp_ai_assistant;
GRANT SELECT  ON dbo.dim_UserRoles              TO tcp_ai_assistant;
GRANT SELECT  ON dbo.dim_Employees              TO tcp_ai_assistant;
GO

-- tcp_generator: write fact_Trades + fact_DailyTraderPnL; read dims/config; execute calendar fns.
GRANT EXECUTE ON dbo.usp_GenerateDailyTrades   TO tcp_generator;
GRANT INSERT, UPDATE ON dbo.fact_Trades        TO tcp_generator;
GRANT INSERT, UPDATE ON dbo.fact_DailyTraderPnL TO tcp_generator;
GRANT SELECT  ON dbo.fact_Trades               TO tcp_generator;
GRANT SELECT  ON dbo.fact_DailyTraderPnL       TO tcp_generator;
GRANT SELECT  ON dbo.dim_Employees             TO tcp_generator;
GRANT SELECT  ON dbo.dim_Accounts              TO tcp_generator;
GRANT SELECT  ON dbo.dim_Markets               TO tcp_generator;
GRANT SELECT  ON dbo.dim_Sessions              TO tcp_generator;
GRANT SELECT  ON dbo.dim_OrderType             TO tcp_generator;
GRANT SELECT  ON dbo.dim_Date                  TO tcp_generator;
GRANT SELECT  ON dbo.dim_UserRoles             TO tcp_generator;
GRANT SELECT  ON dbo.config_Capital            TO tcp_generator;
-- Scalar function uses EXECUTE; inline TVF uses SELECT -- do not unify.
GRANT EXECUTE ON dbo.fn_GetCapitalBaseline     TO tcp_generator;
GRANT SELECT  ON dbo.tvf_GetCapitalBaseline    TO tcp_generator;
GRANT EXECUTE ON dbo.fn_IsTradingDay           TO tcp_generator;
GRANT EXECUTE ON dbo.fn_PreviousBusinessDay    TO tcp_generator;
GO

-- tcp_bi_reader: PowerBI Service principal -- views + dim tables (relationship engine).
GRANT SELECT ON dbo.v_trades_enriched      TO tcp_bi_reader;
GRANT SELECT ON dbo.v_employee_performance TO tcp_bi_reader;
GRANT SELECT ON dbo.v_team_performance     TO tcp_bi_reader;
GRANT SELECT ON dbo.v_floor_performance    TO tcp_bi_reader;
GRANT SELECT ON dbo.v_daily_pnl            TO tcp_bi_reader;
GRANT SELECT ON dbo.fact_DailyTraderPnL    TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Employees          TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Teams              TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_TradingFloors      TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Markets            TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Sessions           TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_OrderType          TO tcp_bi_reader;
GRANT SELECT ON dbo.dim_Date               TO tcp_bi_reader;
GO

-- tcp_admin: full read/write/EXECUTE (thesis-context convenience; documented).
-- Guards keep the role-member adds idempotent on re-apply (Msg 15410 otherwise).
IF IS_ROLEMEMBER(N'db_datareader', N'tcp_admin') = 0
    ALTER ROLE db_datareader ADD MEMBER tcp_admin;
IF IS_ROLEMEMBER(N'db_datawriter', N'tcp_admin') = 0
    ALTER ROLE db_datawriter ADD MEMBER tcp_admin;
GRANT EXECUTE ON SCHEMA::dbo TO tcp_admin;
GO

-- ============ 13. RECORD MIGRATION ============
-- The checksum literal `__V001_CHECKSUM__` is a placeholder that
-- `infra/scripts/postprovision.{ps1,sh}` Step 0 replaces with the SHA-256
-- value computed by `scripts/compute_migration_checksum.py` before piping the
-- file to sqlcmd. Local Docker-based applies (db/README.md) leave the
-- placeholder intact; the cd.yml post-deploy assertion catches the sentinel.
--
-- MERGE WITH (HOLDLOCK) per code-MA-03: closes the classic IF NOT EXISTS /
-- INSERT / ELSE UPDATE race under any concurrent apply. The HOLDLOCK hint
-- takes a key-range lock on `script_name` so a second session blocks until
-- the first commits, eliminating the PK_schema_history violation path.
-- N'…' (nvarchar) literals harmonise with the NVARCHAR(200) script_name
-- column type and the V002 migration's literal style.
MERGE dbo.schema_history WITH (HOLDLOCK) AS target
USING (VALUES (N'V001__init.sql', SYSUTCDATETIME(), N'__V001_CHECKSUM__'))
   AS source(script_name, applied_at_utc, checksum)
   ON (target.script_name = source.script_name)
WHEN MATCHED AND target.checksum <> source.checksum THEN
    -- Refresh checksum on re-apply after a substantive edit; preserve
    -- applied_at_utc so the FIRST-applied timestamp is durable.
    UPDATE SET checksum = source.checksum
WHEN NOT MATCHED THEN
    INSERT (script_name, applied_at_utc, checksum)
    VALUES (source.script_name, source.applied_at_utc, source.checksum);
GO

PRINT 'V001__init.sql applied successfully.';
GO
