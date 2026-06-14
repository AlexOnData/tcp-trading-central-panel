-- =====================================================================
-- TCP -- Trading Central Panel
-- Migration: V003__rls_db_owner_bypass.sql
-- Author:    Etapa 14 PART B.4 (PowerBI Desktop deploy completion)
-- Depends:   V001__init.sql (rls.fn_TradesPredicate, rls.TradesAccessPolicy).
-- ADRs:      ADR-003 (RLS via SESSION_CONTEXT; admin scope bypasses BLOCK predicate).
--
-- Purpose:
--   Add a db_owner short-circuit branch to rls.fn_TradesPredicate so that
--   admin connections (PowerBI Desktop / sqlcmd / SSMS as AAD admin) can read
--   the RLS-protected fact tables (fact_Trades, fact_DailyTraderPnL) without
--   needing to manually set SESSION_CONTEXT(N'aad_object_id').
--
--   Context: the Function App sets SESSION_CONTEXT per-request via
--   sp_set_session_context before any query. PowerBI Desktop's OAuth pool does
--   NOT set this. Pre-V003, admin users got 0 rows from fact_* (the predicate
--   found nothing in dim_UserRoles for a NULL aad_object_id) and downstream
--   v_* views returned 0 rows on PowerBI refresh.
--
--   V003 preserves the original SESSION_CONTEXT branch unchanged (Function App
--   path) and adds a UNION ALL branch that permits db_owner members. Practical
--   effect: AAD admins, dbo, and any role member of db_owner get full read
--   access; everyone else still goes through the original predicate.
--
-- Idempotency:
--   * The DROP/CREATE sequence is wrapped in IF EXISTS / IF NOT EXISTS checks.
--   * Re-applying V003 against an already-V003 database is a no-op for the
--     security policy state (final STATE = ON) and for the schema_history
--     ledger (MERGE WITH (HOLDLOCK) idempotent).
--   * The function body is replaced with CREATE OR ALTER inside the gap
--     between DROP POLICY and CREATE POLICY — same content on re-apply.
--
-- Security trade-off (documented in thesis Capitolul 6):
--   The bypass widens the RLS exemption from "SESSION_CONTEXT-mediated admin
--   scope" (V001 behavior) to "db_owner role membership" (V003 behavior).
--   This is acceptable for this project because:
--     - db_owner on Azure SQL is gated by AAD admin assignment on the server
--       (or explicit ALTER ROLE membership) which the deploy controls.
--     - The Function App MI is NOT db_owner (it has tcp_generator + ai roles
--       only), so its access still goes through SESSION_CONTEXT.
--     - The PowerBI SP `tcp-powerbi-sp` is NOT db_owner (it has tcp_bi_reader
--       only), so SP-based Service refresh would still be filtered — admin
--       must use AAD user OAuth credentials in PowerBI Service for refresh.
--
-- Why DROP/CREATE instead of plain ALTER:
--   SCHEMABINDING on rls.fn_TradesPredicate means the function cannot be
--   altered while a Security Policy references it (Msg 3729). The published
--   pattern in Microsoft docs is DROP POLICY -> ALTER FUNCTION -> CREATE
--   POLICY. There is a brief window (<1 sec) where RLS is unenforced; for a
--   migration applied during deploy (no concurrent traffic) this is safe.
-- =====================================================================

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- ============ 1. DROP existing security policy (so we can ALTER the function) ============
IF EXISTS (
    SELECT 1
    FROM sys.security_policies sp
    JOIN sys.schemas s ON s.schema_id = sp.schema_id
    WHERE sp.name = N'TradesAccessPolicy'
      AND s.name  = N'rls'
)
BEGIN
    DROP SECURITY POLICY rls.TradesAccessPolicy;
END;
GO

-- ============ 2. ALTER RLS predicate function with db_owner bypass ============
CREATE OR ALTER FUNCTION rls.fn_TradesPredicate(@trader_id_in_row INT)
RETURNS TABLE
WITH SCHEMABINDING
AS
RETURN
    -- Branch A: db_owner short-circuit (admin connections, no SESSION_CONTEXT needed).
    SELECT 1 AS result
    FROM (VALUES (1)) AS dummy(x)
    WHERE IS_MEMBER('db_owner') = 1

    UNION ALL

    -- Branch B: original SESSION_CONTEXT-mediated predicate (Function App path).
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

-- ============ 3. RECREATE security policy with original bindings ============
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

-- ============ 4. RECORD MIGRATION ============
-- The checksum literal `__V003_CHECKSUM__` is replaced at apply time by
-- `infra/scripts/postprovision.{ps1,sh}` Step 0 with the SHA-256 value
-- computed by `scripts/compute_migration_checksum.py`. See V001 for rationale.
-- MERGE WITH (HOLDLOCK) per code-MA-03 mirrors V001/V002's pattern.
MERGE dbo.schema_history WITH (HOLDLOCK) AS target
USING (VALUES (N'V003__rls_db_owner_bypass.sql', SYSUTCDATETIME(), N'__V003_CHECKSUM__'))
   AS source(script_name, applied_at_utc, checksum)
   ON (target.script_name = source.script_name)
WHEN MATCHED AND target.checksum <> source.checksum THEN
    UPDATE SET checksum = source.checksum
WHEN NOT MATCHED THEN
    INSERT (script_name, applied_at_utc, checksum)
    VALUES (source.script_name, source.applied_at_utc, source.checksum);
GO

PRINT 'V003__rls_db_owner_bypass.sql applied successfully.';
GO
