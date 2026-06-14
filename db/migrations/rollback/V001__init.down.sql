-- =====================================================================
-- TCP -- Trading Central Panel
-- Rollback for V001__init.sql
--
-- WARNING: DESTRUCTIVE. This script drops every object (and therefore
-- every row of data) created by V001__init.sql.
-- Use only for a clean re-bootstrap of a non-production database.
-- The production rollback path is point-in-time restore from PITR
-- (see docs/decisions/ADR-004-bacpac-export-schedule.md and §12 of
-- docs/design/02_database_design.md).
--
-- Idempotent: every drop is guarded; re-running on an already-cleaned
-- database is a no-op.
-- Apply with: sqlcmd -S <server> -d <db> -G -i V001__init.down.sql -b
-- =====================================================================

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

-- ============ 1. SECURITY POLICY ============
IF EXISTS (
    SELECT 1
    FROM sys.security_policies sp
    JOIN sys.schemas s ON s.schema_id = sp.schema_id
    WHERE sp.name = N'TradesAccessPolicy'
      AND s.name  = N'rls'
)
    DROP SECURITY POLICY rls.TradesAccessPolicy;
GO

-- ============ 2. RLS PREDICATE FUNCTION ============
IF OBJECT_ID(N'rls.fn_TradesPredicate', N'IF') IS NOT NULL
    DROP FUNCTION rls.fn_TradesPredicate;
GO

-- ============ 3. FACT TABLES ============
IF OBJECT_ID(N'dbo.fact_DailyTraderPnL', N'U') IS NOT NULL
    DROP TABLE dbo.fact_DailyTraderPnL;
GO

IF OBJECT_ID(N'dbo.fact_Trades', N'U') IS NOT NULL
    DROP TABLE dbo.fact_Trades;
GO

-- ============ 4. VIEWS ============
IF OBJECT_ID(N'dbo.v_daily_pnl', N'V') IS NOT NULL
    DROP VIEW dbo.v_daily_pnl;
GO
IF OBJECT_ID(N'dbo.v_floor_performance', N'V') IS NOT NULL
    DROP VIEW dbo.v_floor_performance;
GO
IF OBJECT_ID(N'dbo.v_team_performance', N'V') IS NOT NULL
    DROP VIEW dbo.v_team_performance;
GO
IF OBJECT_ID(N'dbo.v_employee_performance', N'V') IS NOT NULL
    DROP VIEW dbo.v_employee_performance;
GO
IF OBJECT_ID(N'dbo.v_trades_enriched', N'V') IS NOT NULL
    DROP VIEW dbo.v_trades_enriched;
GO

-- ============ 5. STORED PROCEDURES ============
IF OBJECT_ID(N'dbo.usp_GetTopPerformers', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_GetTopPerformers;
GO
IF OBJECT_ID(N'dbo.usp_GetEmployeePerformance', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_GetEmployeePerformance;
GO
IF OBJECT_ID(N'dbo.usp_GenerateDailyTrades', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_GenerateDailyTrades;
GO

-- ============ 6. FUNCTIONS ============
IF OBJECT_ID(N'dbo.tvf_RiskMetrics', N'IF') IS NOT NULL
    DROP FUNCTION dbo.tvf_RiskMetrics;
GO
IF OBJECT_ID(N'dbo.fn_PreviousBusinessDay', N'FN') IS NOT NULL
    DROP FUNCTION dbo.fn_PreviousBusinessDay;
GO
IF OBJECT_ID(N'dbo.fn_IsTradingDay', N'FN') IS NOT NULL
    DROP FUNCTION dbo.fn_IsTradingDay;
GO
IF OBJECT_ID(N'dbo.tvf_GetCapitalBaseline', N'IF') IS NOT NULL
    DROP FUNCTION dbo.tvf_GetCapitalBaseline;
GO
IF OBJECT_ID(N'dbo.fn_GetCapitalBaseline', N'FN') IS NOT NULL
    DROP FUNCTION dbo.fn_GetCapitalBaseline;
GO

-- ============ 7. CONFIG + DIMENSION TABLES ============
IF OBJECT_ID(N'dbo.config_Capital', N'U') IS NOT NULL
    DROP TABLE dbo.config_Capital;
GO
IF OBJECT_ID(N'dbo.dim_UserRoles', N'U') IS NOT NULL
    DROP TABLE dbo.dim_UserRoles;
GO
IF OBJECT_ID(N'dbo.dim_Date', N'U') IS NOT NULL
    DROP TABLE dbo.dim_Date;
GO
IF OBJECT_ID(N'dbo.dim_OrderType', N'U') IS NOT NULL
    DROP TABLE dbo.dim_OrderType;
GO
IF OBJECT_ID(N'dbo.dim_Sessions', N'U') IS NOT NULL
    DROP TABLE dbo.dim_Sessions;
GO
IF OBJECT_ID(N'dbo.dim_Markets', N'U') IS NOT NULL
    DROP TABLE dbo.dim_Markets;
GO
IF OBJECT_ID(N'dbo.dim_Accounts', N'U') IS NOT NULL
    DROP TABLE dbo.dim_Accounts;
GO
IF OBJECT_ID(N'dbo.dim_Employees', N'U') IS NOT NULL
    DROP TABLE dbo.dim_Employees;
GO
IF OBJECT_ID(N'dbo.dim_Teams', N'U') IS NOT NULL
    DROP TABLE dbo.dim_Teams;
GO
IF OBJECT_ID(N'dbo.dim_TradingFloors', N'U') IS NOT NULL
    DROP TABLE dbo.dim_TradingFloors;
GO
IF OBJECT_ID(N'dbo.dim_Companies', N'U') IS NOT NULL
    DROP TABLE dbo.dim_Companies;
GO

-- ============ 8. ROLES ============
IF DATABASE_PRINCIPAL_ID(N'tcp_admin') IS NOT NULL
BEGIN
    IF IS_ROLEMEMBER('db_datareader', 'tcp_admin') = 1
        ALTER ROLE db_datareader DROP MEMBER tcp_admin;
    IF IS_ROLEMEMBER('db_datawriter', 'tcp_admin') = 1
        ALTER ROLE db_datawriter DROP MEMBER tcp_admin;
    DROP ROLE tcp_admin;
END;
GO
IF DATABASE_PRINCIPAL_ID(N'tcp_bi_reader') IS NOT NULL
    DROP ROLE tcp_bi_reader;
GO
IF DATABASE_PRINCIPAL_ID(N'tcp_generator') IS NOT NULL
    DROP ROLE tcp_generator;
GO
IF DATABASE_PRINCIPAL_ID(N'tcp_ai_assistant') IS NOT NULL
    DROP ROLE tcp_ai_assistant;
GO

-- ============ 9. SCHEMAS ============
IF SCHEMA_ID(N'rls') IS NOT NULL
    EXEC(N'DROP SCHEMA rls;');
GO

-- ============ 10. SCHEMA HISTORY ROW ============
IF OBJECT_ID(N'dbo.schema_history', N'U') IS NOT NULL
    DELETE FROM dbo.schema_history WHERE script_name = 'V001__init.sql';
GO

PRINT 'V001__init.down.sql applied successfully.';
GO
