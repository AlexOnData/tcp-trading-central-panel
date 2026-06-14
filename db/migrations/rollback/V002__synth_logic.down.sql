-- =====================================================================
-- TCP -- Trading Central Panel
-- Rollback: V002__synth_logic.down.sql
-- Reverts V002__synth_logic.sql by restoring the V001 stub body of
-- dbo.usp_GenerateDailyTrades verbatim and removing the V002 row from
-- dbo.schema_history.
--
-- Idempotent: re-running this script against a database that has already
-- been rolled back is a no-op (CREATE OR ALTER + IF EXISTS guards).
--
-- Note:
--   The V001 grant `GRANT EXECUTE ON dbo.usp_GenerateDailyTrades TO tcp_generator`
--   persists across CREATE OR ALTER (same object_id), so no re-grant is needed.
-- =====================================================================

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

-- Pre-flight: schema_history must exist for the rollback ledger update.
IF OBJECT_ID(N'dbo.schema_history', N'U') IS NULL
    THROW 50200, 'V002 rollback cannot apply: dbo.schema_history is missing.', 1;
GO

-- ============ 1. RESTORE V001 STUB BODY OF usp_GenerateDailyTrades ============
-- Pasted verbatim from V001__init.sql (Etapa 2 scaffolding). Do not modify here
-- without updating V001 in lockstep; this rollback exists to bring the database
-- back to the post-V001 state.

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

-- ============ 2. REMOVE V002 ROW FROM schema_history ============
IF EXISTS (SELECT 1 FROM dbo.schema_history WHERE script_name = N'V002__synth_logic.sql')
    DELETE FROM dbo.schema_history WHERE script_name = N'V002__synth_logic.sql';
GO

PRINT 'V002__synth_logic.down.sql applied successfully.';
GO
