EXEC sp_set_session_context @key = N'aad_object_id', @value = '00000000-0000-0000-0000-000000000000'; -- replace with your AAD object ID

SELECT
  (SELECT COUNT(*) FROM dbo.fact_Trades) AS trades,
  (SELECT COUNT(DISTINCT trade_date_ro) FROM dbo.fact_Trades) AS dates,
  (SELECT MIN(trade_date_ro) FROM dbo.fact_Trades) AS earliest,
  (SELECT MAX(trade_date_ro) FROM dbo.fact_Trades) AS latest,
  (SELECT COUNT(DISTINCT trader_id) FROM dbo.fact_Trades) AS distinct_traders,
  (SELECT COUNT(*) FROM dbo.fact_DailyTraderPnL) AS daily_pnl_rows,
  CAST((SELECT SUM(net_pnl_eur) FROM dbo.fact_Trades WHERE is_open = 0) AS DECIMAL(18,2)) AS total_net_pnl_eur;
