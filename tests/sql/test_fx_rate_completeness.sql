-- =====================================================================
-- FX-rate completeness invariant (per V001 fact_Trades application-layer rule).
--
-- Rule: for every CLOSED trade (is_open = 0) whose market quote currency
-- is not EUR, fx_rate_to_eur MUST be non-NULL (so gross_pnl_eur and
-- net_pnl_eur are reproducible from price_entry/price_exit).
--
-- This invariant is NOT enforced by a CHECK constraint or trigger to keep
-- the synthetic-data write path simple; CI guards it here.
--
-- Execution-context contract: this test queries dbo.fact_Trades directly
-- and therefore MUST be run as `tcp_admin` (or the bootstrap server admin).
-- Roles without direct SELECT on dbo.fact_Trades (notably tcp_ai_assistant,
-- which is intentionally fact_Trades-blind) would see zero rows even when
-- the invariant is violated, masking real defects. CI executes this test
-- under the admin connection string; do NOT switch the runner to a lower
-- role. A future hardening pass may rewrite the query against an
-- admin-only diagnostics view (e.g. v_trades_diagnostics) -- tracked in
-- review_etapa2_sql_pass1.md MN-11.
--
-- Exits non-zero (RAISERROR severity 16) on any violation.
-- =====================================================================

SET NOCOUNT ON;
GO

DECLARE @bad_rows INT;

SELECT @bad_rows = COUNT(*)
FROM dbo.fact_Trades AS f
JOIN dbo.dim_Markets AS m ON m.market_id = f.market_id
WHERE f.is_open         = 0
  AND m.quote_currency <> 'EUR'
  AND f.fx_rate_to_eur IS NULL;

IF @bad_rows > 0
    RAISERROR('fx_rate_completeness violation: %d closed non-EUR trades without fx_rate_to_eur.', 16, 1, @bad_rows);
ELSE
    PRINT 'fx_rate_completeness: OK';
GO
