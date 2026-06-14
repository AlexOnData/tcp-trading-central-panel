-- =====================================================================
-- TCP -- Trading Central Panel
-- Migration: V002__synth_logic.sql
-- Author:    Etapa 3 (synthesis logic)
-- Depends:   V001__init.sql (fact_Trades, fact_DailyTraderPnL, fn_IsTradingDay,
--            schema_history, role tcp_generator, RLS policy rls.policy_TradesRLS).
-- ADRs:      ADR-002 (daily-PnL materialisation via MERGE),
--            ADR-003 (RLS via SESSION_CONTEXT; admin scope bypasses BLOCK predicate).
--
-- Purpose:
--   Replace the V001 scaffold of dbo.usp_GenerateDailyTrades with the production
--   body that ingests an OPENJSON payload of synthesised trades, inserts them
--   into fact_Trades, and materialises the per-trader daily aggregate into
--   fact_DailyTraderPnL atomically.
--
-- Idempotency:
--   * Re-applying V002 against an already-V002 database is a no-op for objects
--     (CREATE OR ALTER) and for the schema_history ledger (guarded by IF NOT EXISTS).
--   * The proc itself is idempotent on (@trade_date): when fact_Trades already
--     contains rows for @trade_date, it short-circuits with status='already_generated'.
--
-- Contract (returned single result-set, exactly one row):
--   rows_inserted INT, status NVARCHAR(40)
--   status in: 'ok', 'already_generated', 'skipped_non_trading_day'.
--
-- Permissions:
--   The V001 grant `GRANT EXECUTE ON dbo.usp_GenerateDailyTrades TO tcp_generator`
--   persists across CREATE OR ALTER (the object_id is preserved). No re-grant needed.
-- =====================================================================

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

-- ============ 1. PRE-FLIGHT DEPENDENCY CHECKS ============
-- Fail fast if V001 has not been applied -- V002 has a hard dependency on it.

IF OBJECT_ID(N'dbo.fact_Trades', N'U') IS NULL
    THROW 50100, 'V002 cannot apply: dbo.fact_Trades is missing. Apply V001__init.sql first.', 1;
IF OBJECT_ID(N'dbo.fact_DailyTraderPnL', N'U') IS NULL
    THROW 50101, 'V002 cannot apply: dbo.fact_DailyTraderPnL is missing. Apply V001__init.sql first.', 1;
IF OBJECT_ID(N'dbo.fn_IsTradingDay', N'FN') IS NULL
    THROW 50102, 'V002 cannot apply: dbo.fn_IsTradingDay is missing. Apply V001__init.sql first.', 1;
IF OBJECT_ID(N'dbo.schema_history', N'U') IS NULL
    THROW 50103, 'V002 cannot apply: dbo.schema_history is missing. Apply V001__init.sql first.', 1;
GO

-- ============ 2. usp_GenerateDailyTrades (production body) ============

--/ Generate one trading day's worth of fact_Trades rows from an OPENJSON payload
--/ and materialise the per-trader daily aggregate into fact_DailyTraderPnL.
--/ The proc is idempotent on (@trade_date) and runs INSERT + MERGE atomically.
CREATE OR ALTER PROCEDURE dbo.usp_GenerateDailyTrades
    @trade_date DATE,
    @trades     NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @rows_inserted INT           = 0;
    DECLARE @status        NVARCHAR(40)  = N'unknown';

    -- ---------- 2.1 parameter validation ----------
    IF @trade_date IS NULL
        THROW 50001, 'usp_GenerateDailyTrades: @trade_date is required.', 1;

    -- Reject weekends and RO public holidays (delegated to fn_IsTradingDay).
    -- Short-circuit BEFORE opening a transaction -- skipping is not an error.
    IF dbo.fn_IsTradingDay(@trade_date) = 0
    BEGIN
        SELECT
            CAST(0 AS INT)                          AS rows_inserted,
            CAST(N'skipped_non_trading_day' AS NVARCHAR(40)) AS [status];
        RETURN 0;
    END;

    -- Idempotency probe -- BEFORE the transaction and BEFORE parsing JSON, so a
    -- replay of the same day is cheap and side-effect-free.
    IF EXISTS (SELECT 1 FROM dbo.fact_Trades WHERE trade_date_ro = @trade_date)
    BEGIN
        SELECT
            CAST((SELECT COUNT(*) FROM dbo.fact_Trades WHERE trade_date_ro = @trade_date) AS INT)
                                                    AS rows_inserted,
            CAST(N'already_generated' AS NVARCHAR(40)) AS [status];
        RETURN 0;
    END;

    -- Payload presence + well-formedness checks (cheap; outside the transaction).
    IF @trades IS NULL OR DATALENGTH(@trades) < 4
        THROW 50110, 'usp_GenerateDailyTrades: @trades JSON payload is empty.', 1;
    IF ISJSON(@trades) <> 1
        THROW 50111, 'usp_GenerateDailyTrades: @trades is not valid JSON.', 1;

    -- ---------- 2.2 staging table-variable (typed projection of the JSON payload) ----------
    DECLARE @parsed TABLE
    (
        trade_uid       VARCHAR(14)        NOT NULL PRIMARY KEY,
        trader_id       INT                NOT NULL,
        account_id      INT                NOT NULL,
        market_id       INT                NOT NULL,
        session_id      INT                NOT NULL,
        order_type_id   INT                NOT NULL,
        side            CHAR(1)            NOT NULL,
        quantity        DECIMAL(18,4)      NOT NULL,
        price_entry     DECIMAL(18,6)      NOT NULL,
        price_exit      DECIMAL(18,6)      NULL,
        time_entry      DATETIMEOFFSET(3)  NOT NULL,
        time_exit       DATETIMEOFFSET(3)  NULL,
        gross_pnl_eur   DECIMAL(18,4)      NULL,
        commission_eur  DECIMAL(18,4)      NOT NULL,
        net_pnl_eur     DECIMAL(18,4)      NULL,
        is_open         BIT                NOT NULL,
        fx_rate_to_eur  DECIMAL(18,8)      NULL
    );

    BEGIN TRY
        -- Parse the JSON outside the transaction -- this is read-only and the
        -- table-variable is not transactional, so we save lock duration. Any
        -- parse-time failure surfaces in the CATCH below before we open a tran.
        INSERT INTO @parsed
        (
            trade_uid, trader_id, account_id, market_id, session_id, order_type_id,
            side, quantity, price_entry, price_exit, time_entry, time_exit,
            gross_pnl_eur, commission_eur, net_pnl_eur, is_open, fx_rate_to_eur
        )
        SELECT
            j.trade_uid,
            j.trader_id,
            j.account_id,
            j.market_id,
            j.session_id,
            j.order_type_id,
            j.side,
            j.quantity,
            j.price_entry,
            j.price_exit,
            -- OPENJSON does not natively bind DATETIMEOFFSET in the WITH clause,
            -- so we read as NVARCHAR and TRY_CAST. A NULL on a NOT NULL column
            -- will raise on INSERT into @parsed, which is the intended behaviour.
            TRY_CAST(j.time_entry AS DATETIMEOFFSET(3)) AS time_entry,
            TRY_CAST(j.time_exit  AS DATETIMEOFFSET(3)) AS time_exit,
            j.gross_pnl_eur,
            j.commission_eur,
            j.net_pnl_eur,
            j.is_open,
            j.fx_rate_to_eur
        FROM OPENJSON(@trades) WITH
        (
            trade_uid       VARCHAR(14)    '$.trade_uid',
            trader_id       INT            '$.trader_id',
            account_id      INT            '$.account_id',
            market_id       INT            '$.market_id',
            session_id      INT            '$.session_id',
            order_type_id   INT            '$.order_type_id',
            side            CHAR(1)        '$.side',
            quantity        DECIMAL(18,4)  '$.quantity',
            price_entry     DECIMAL(18,6)  '$.price_entry',
            price_exit      DECIMAL(18,6)  '$.price_exit',
            time_entry      NVARCHAR(40)   '$.time_entry',
            time_exit       NVARCHAR(40)   '$.time_exit',
            gross_pnl_eur   DECIMAL(18,4)  '$.gross_pnl_eur',
            commission_eur  DECIMAL(18,4)  '$.commission_eur',
            net_pnl_eur     DECIMAL(18,4)  '$.net_pnl_eur',
            is_open         BIT            '$.is_open',
            fx_rate_to_eur  DECIMAL(18,8)  '$.fx_rate_to_eur'
        ) AS j;

        -- Empty payload (valid JSON but zero rows) is rejected -- a no-op call
        -- with `@trades = '[]'` would otherwise leave a confusing 'ok'/0 result.
        IF NOT EXISTS (SELECT 1 FROM @parsed)
            THROW 50112, 'usp_GenerateDailyTrades: @trades parsed to zero rows.', 1;

        -- Cross-row invariant: every row's time_entry must fall on @trade_date
        -- when projected to Europe/Bucharest local date. Catches off-by-one
        -- DST mistakes from the caller before they hit the PERSISTED column.
        IF EXISTS (
            SELECT 1
            FROM @parsed
            WHERE CAST(time_entry AT TIME ZONE 'E. Europe Standard Time' AS DATE) <> @trade_date
        )
            THROW 50113, 'usp_GenerateDailyTrades: one or more rows have time_entry whose Europe/Bucharest date does not equal @trade_date.', 1;

        -- ---------- 2.3 atomic INSERT + MERGE ----------
        BEGIN TRANSACTION;

        INSERT INTO dbo.fact_Trades
        (
            trade_uid, trader_id, account_id, market_id, session_id, order_type_id,
            side, quantity, price_entry, price_exit, time_entry, time_exit,
            gross_pnl_eur, commission_eur, net_pnl_eur, is_open, fx_rate_to_eur
        )
        SELECT
            trade_uid, trader_id, account_id, market_id, session_id, order_type_id,
            side, quantity, price_entry, price_exit, time_entry, time_exit,
            gross_pnl_eur, commission_eur, net_pnl_eur, is_open, fx_rate_to_eur
        FROM @parsed;

        SET @rows_inserted = @@ROWCOUNT;

        -- MERGE per ADR-002. Source aggregates only CLOSED legs (gross/net PnL
        -- are NULL for open positions per CK_fact_Trades_open_closed); ISNULL
        -- keeps any open trades from poisoning the SUMs while their counts
        -- still contribute to trade_count.
        MERGE INTO dbo.fact_DailyTraderPnL AS tgt
        USING
        (
            SELECT
                p.trader_id                                                                AS employee_id,
                @trade_date                                                                AS trade_date_ro,
                COUNT(*)                                                                    AS trade_count,
                SUM(ISNULL(p.gross_pnl_eur, 0))                                             AS gross_pnl_eur_total,
                SUM(ISNULL(p.net_pnl_eur,   0))                                             AS net_pnl_eur_total,
                SUM(p.commission_eur)                                                       AS commission_eur_total,
                SUM(CASE WHEN p.net_pnl_eur > 0 THEN 1 ELSE 0 END)                          AS win_count,
                SUM(CASE WHEN p.net_pnl_eur < 0 THEN 1 ELSE 0 END)                          AS loss_count,
                AVG(CASE WHEN p.time_exit IS NOT NULL
                         THEN CAST(DATEDIFF(MINUTE, p.time_entry, p.time_exit) AS DECIMAL(18,4))
                         ELSE NULL END)                                                     AS avg_holding_minutes
            FROM @parsed AS p
            GROUP BY p.trader_id
        ) AS src
            ON  tgt.employee_id   = src.employee_id
            AND tgt.trade_date_ro = src.trade_date_ro
        WHEN MATCHED THEN
            UPDATE SET
                trade_count          = src.trade_count,
                gross_pnl_eur_total  = src.gross_pnl_eur_total,
                net_pnl_eur_total    = src.net_pnl_eur_total,
                commission_eur_total = src.commission_eur_total,
                win_count            = src.win_count,
                loss_count           = src.loss_count,
                avg_holding_minutes  = src.avg_holding_minutes,
                updated_at           = SYSDATETIMEOFFSET()
        WHEN NOT MATCHED BY TARGET THEN
            INSERT
            (
                employee_id, trade_date_ro, trade_count, gross_pnl_eur_total, net_pnl_eur_total,
                commission_eur_total, win_count, loss_count, avg_holding_minutes
            )
            VALUES
            (
                src.employee_id, src.trade_date_ro, src.trade_count, src.gross_pnl_eur_total, src.net_pnl_eur_total,
                src.commission_eur_total, src.win_count, src.loss_count, src.avg_holding_minutes
            );

        COMMIT TRANSACTION;

        SET @status = N'ok';

        SELECT
            CAST(@rows_inserted AS INT)              AS rows_inserted,
            CAST(@status        AS NVARCHAR(40))     AS [status];
        RETURN 0;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;

        DECLARE @err_num     INT            = ERROR_NUMBER();
        DECLARE @err_msg     NVARCHAR(2048) = ERROR_MESSAGE();
        DECLARE @err_line    INT            = ERROR_LINE();
        DECLARE @err_proc    NVARCHAR(200)  = ISNULL(ERROR_PROCEDURE(), N'<adhoc>');
        DECLARE @wrapped     NVARCHAR(2400) =
            N'usp_GenerateDailyTrades failed (err=' + CAST(@err_num AS NVARCHAR(10))
          + N', line=' + CAST(@err_line AS NVARCHAR(10))
          + N', proc=' + @err_proc + N'): ' + @err_msg;

        -- Re-raise as severity 16, state 1 so the caller sees one consistent code.
        THROW 50199, @wrapped, 1;
    END CATCH
END;
GO

-- ============ 3. RECORD MIGRATION ============
-- The checksum literal `__V002_CHECKSUM__` is replaced at apply time by
-- `infra/scripts/postprovision.{ps1,sh}` Step 0 with the SHA-256 value
-- computed by `scripts/compute_migration_checksum.py`. See V001 for rationale.
-- MERGE WITH (HOLDLOCK) per code-MA-03 mirrors V001's pattern.
MERGE dbo.schema_history WITH (HOLDLOCK) AS target
USING (VALUES (N'V002__synth_logic.sql', SYSUTCDATETIME(), N'__V002_CHECKSUM__'))
   AS source(script_name, applied_at_utc, checksum)
   ON (target.script_name = source.script_name)
WHEN MATCHED AND target.checksum <> source.checksum THEN
    UPDATE SET checksum = source.checksum
WHEN NOT MATCHED THEN
    INSERT (script_name, applied_at_utc, checksum)
    VALUES (source.script_name, source.applied_at_utc, source.checksum);
GO

PRINT 'V002__synth_logic.sql applied successfully.';
GO
