-- =====================================================================
-- RLS smoke test (per docs/decisions/ADR-003-rls-session-context.md).
--
-- Five test cases:
--   TC-1: SESSION_CONTEXT unset -> deny-by-default; v_trades_enriched empty.
--   TC-2: scope='trader' -> only the principal's own trader row is visible.
--   TC-3: scope='admin'  -> all rows visible.
--   TC-4: read_only_lock -> a second sp_set_session_context on the same key
--         without override fails with error 15664 (ADR-003 §2).
--   TC-5: block_insert_other_trader -> while under trader scope, an INSERT
--         attributed to a different trader_id fails with error 33504
--         (BLOCK PREDICATE AFTER INSERT, ADR-003 + 02_database_design §9.3).
--
-- Each block is non-destructive: all writes happen inside a single
-- transaction that is rolled back at the end. The test inserts a fresh
-- dim_Employees row, a dim_Accounts row, two fact_Trades rows, and a
-- dim_UserRoles row, exercises the predicate, then rolls back.
--
-- Exits non-zero (RAISERROR severity 16) on any assertion failure.
-- =====================================================================

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

BEGIN TRY
    BEGIN TRAN;

    -- Pin a known principal AAD object id for this test run.
    DECLARE @principal_oid UNIQUEIDENTIFIER = NEWID();
    DECLARE @other_oid     UNIQUEIDENTIFIER = NEWID();

    -- We need two distinct employee rows in fact_Trades. Insert two fresh
    -- traders to keep this test independent of any seeded employees.
    DECLARE @principal_emp_id INT;
    DECLARE @other_emp_id     INT;

    INSERT INTO dbo.dim_Employees
        (company_id, floor_id, team_id, first_name, last_name, email, employee_role, hire_date, aad_object_id)
    VALUES
        (1, 1, 1, N'RLS', N'Principal', N'rls.principal@tcp-capital.ro', 'trader', '2024-01-02', @principal_oid);
    SET @principal_emp_id = SCOPE_IDENTITY();

    INSERT INTO dbo.dim_Employees
        (company_id, floor_id, team_id, first_name, last_name, email, employee_role, hire_date, aad_object_id)
    VALUES
        (1, 1, 1, N'RLS', N'Other',     N'rls.other@tcp-capital.ro',     'trader', '2024-01-02', @other_oid);
    SET @other_emp_id = SCOPE_IDENTITY();

    -- One account per trader. Account currency stays EUR so fx_rate_to_eur stays NULL safely.
    DECLARE @principal_acc_id INT;
    DECLARE @other_acc_id     INT;

    INSERT INTO dbo.dim_Accounts (trader_id, account_code, account_type, currency, opened_on)
    VALUES (@principal_emp_id, 'RLS-PRINCIPAL-001', 'paper', 'EUR', '2024-01-02');
    SET @principal_acc_id = SCOPE_IDENTITY();

    INSERT INTO dbo.dim_Accounts (trader_id, account_code, account_type, currency, opened_on)
    VALUES (@other_emp_id,     'RLS-OTHER-001',     'paper', 'EUR', '2024-01-02');
    SET @other_acc_id = SCOPE_IDENTITY();

    -- Resolve dim ids needed for fact_Trades (market/session/order type).
    DECLARE @market_id  INT = (SELECT TOP 1 market_id      FROM dbo.dim_Markets   WHERE symbol = 'AAPL');
    DECLARE @session_id INT = (SELECT TOP 1 session_id     FROM dbo.dim_Sessions  WHERE session_code = 'regular');
    DECLARE @order_id   INT = (SELECT TOP 1 order_type_id  FROM dbo.dim_OrderType WHERE order_type_code = 'market');

    -- Generator path needs admin scope to BYPASS the BLOCK PREDICATE on insert.
    INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active)
    VALUES (@principal_oid, NULL, 'admin', 1);

    EXEC sp_set_session_context @key=N'aad_object_id', @value=@principal_oid, @read_only=0;

    -- Two closed trades, one per trader, both on a known business day in dim_Date.
    DECLARE @t1 DATETIMEOFFSET(3) = CAST('2024-05-07T10:00:00+02:00' AS DATETIMEOFFSET(3));
    DECLARE @t2 DATETIMEOFFSET(3) = CAST('2024-05-07T11:00:00+02:00' AS DATETIMEOFFSET(3));

    INSERT INTO dbo.fact_Trades
        (trade_uid, trader_id, account_id, market_id, session_id, order_type_id,
         side, quantity, price_entry, price_exit, time_entry, time_exit,
         gross_pnl_eur, commission_eur, net_pnl_eur, is_open)
    VALUES
        ('T20240507-9001', @principal_emp_id, @principal_acc_id, @market_id, @session_id, @order_id,
         'B', 10, 100.000000, 101.000000, @t1, @t2, 10.0000, 0.5000, 9.5000, 0),
        ('T20240507-9002', @other_emp_id,     @other_acc_id,     @market_id, @session_id, @order_id,
         'B', 10, 100.000000, 102.000000, @t1, @t2, 20.0000, 0.5000, 19.5000, 0);

    -- Switch back to a trader scope for the test cases (drop admin row, add trader row).
    UPDATE dbo.dim_UserRoles SET is_active = 0 WHERE aad_object_id = @principal_oid;
    INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active)
    VALUES (@principal_oid, @principal_emp_id, 'trader', 1);

    DECLARE @visible_count INT;

    -- ---------- TC-1: deny-by-default ----------
    EXEC sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0;

    SELECT @visible_count = COUNT(*)
    FROM dbo.v_trades_enriched
    WHERE trade_uid IN ('T20240507-9001', 'T20240507-9002');

    IF @visible_count <> 0
        RAISERROR('TC-1 FAILED: SESSION_CONTEXT unset returned %d rows (expected 0).', 16, 1, @visible_count);
    ELSE
        PRINT 'TC-1 OK: deny-by-default returns 0 rows.';

    -- ---------- TC-2: scope=trader ----------
    EXEC sp_set_session_context @key=N'aad_object_id', @value=@principal_oid, @read_only=0;

    SELECT @visible_count = COUNT(*)
    FROM dbo.v_trades_enriched
    WHERE trade_uid IN ('T20240507-9001', 'T20240507-9002');

    IF @visible_count <> 1
        RAISERROR('TC-2 FAILED: scope=trader returned %d rows (expected 1).', 16, 1, @visible_count);
    ELSE
        PRINT 'TC-2 OK: scope=trader returns exactly 1 row.';

    -- Confirm the visible row is the principal's row, not the other trader's.
    DECLARE @principal_visible INT;
    SELECT @principal_visible = COUNT(*)
    FROM dbo.v_trades_enriched
    WHERE trade_uid = 'T20240507-9001' AND trader_id = @principal_emp_id;

    IF @principal_visible <> 1
        RAISERROR('TC-2 FAILED: principal own row not visible under scope=trader.', 16, 1);

    -- ---------- TC-3: scope=admin ----------
    UPDATE dbo.dim_UserRoles SET is_active = 0 WHERE aad_object_id = @principal_oid;
    INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active)
    VALUES (@principal_oid, NULL, 'admin', 1);

    -- Force a re-read on the same connection (SESSION_CONTEXT value unchanged).
    EXEC sp_set_session_context @key=N'aad_object_id', @value=@principal_oid, @read_only=0;

    SELECT @visible_count = COUNT(*)
    FROM dbo.v_trades_enriched
    WHERE trade_uid IN ('T20240507-9001', 'T20240507-9002');

    IF @visible_count <> 2
        RAISERROR('TC-3 FAILED: scope=admin returned %d rows (expected 2).', 16, 1, @visible_count);
    ELSE
        PRINT 'TC-3 OK: scope=admin returns both rows.';

    PRINT 'RLS smoke: OK';

    -- Always roll back so the test leaves no residual rows.
    IF @@TRANCOUNT > 0 ROLLBACK;
    EXEC sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0;
END TRY
BEGIN CATCH
    DECLARE @err NVARCHAR(2048) = ERROR_MESSAGE();
    IF @@TRANCOUNT > 0 ROLLBACK;
    EXEC sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0;
    RAISERROR('RLS smoke test failed: %s', 16, 1, @err);
END CATCH
GO

-- =====================================================================
-- TC-4: read_only_lock -- a second sp_set_session_context call on the same
-- key without the override must fail with error 15664 ("Cannot set the
-- value of the read-only key"). Confirms that the production code path
-- (@read_only=1 in tcp/db.py) is enforced by the server, not just by
-- convention. Runs inside a rolled-back transaction for symmetry.
-- =====================================================================
BEGIN TRY
    BEGIN TRAN;

    DECLARE @oid_tc4    UNIQUEIDENTIFIER = NEWID();
    DECLARE @oid_tc4_b  UNIQUEIDENTIFIER = NEWID();

    -- First set: lock the key.
    EXEC sp_set_session_context @key=N'aad_object_id', @value=@oid_tc4, @read_only=1;

    DECLARE @caught_error_number INT = 0;

    BEGIN TRY
        -- Second set without the override must raise 15664.
        EXEC sp_set_session_context @key=N'aad_object_id', @value=@oid_tc4_b;
    END TRY
    BEGIN CATCH
        SET @caught_error_number = ERROR_NUMBER();
    END CATCH;

    IF @caught_error_number = 0
        RAISERROR('TC-4 FAILED: second sp_set_session_context on a read-only key did not raise.', 16, 1);
    ELSE IF @caught_error_number <> 15664
        RAISERROR('TC-4 FAILED: expected error 15664, got %d.', 16, 1, @caught_error_number);
    ELSE
        PRINT 'TC-4 OK: read-only key lock enforced (error 15664).';

    IF @@TRANCOUNT > 0 ROLLBACK;
    -- After ROLLBACK the read-only key is reset by the engine; an unguarded
    -- reset call here would itself fail with 15664 if the lock somehow
    -- survived, so we skip the explicit reset.
END TRY
BEGIN CATCH
    DECLARE @err_tc4 NVARCHAR(2048) = ERROR_MESSAGE();
    IF @@TRANCOUNT > 0 ROLLBACK;
    RAISERROR('TC-4 read_only_lock failed: %s', 16, 1, @err_tc4);
END CATCH
GO

-- =====================================================================
-- TC-5: block_insert_other_trader -- while the session is under the
-- principal's trader scope, an INSERT attributed to a different trader_id
-- must be rejected by the BLOCK PREDICATE AFTER INSERT on dbo.fact_Trades
-- (expected error 33504). Runs inside a rolled-back transaction.
-- =====================================================================
BEGIN TRY
    BEGIN TRAN;

    DECLARE @principal_oid_tc5 UNIQUEIDENTIFIER = NEWID();
    DECLARE @other_oid_tc5     UNIQUEIDENTIFIER = NEWID();
    DECLARE @principal_emp_tc5 INT;
    DECLARE @other_emp_tc5     INT;
    DECLARE @principal_acc_tc5 INT;
    DECLARE @other_acc_tc5     INT;

    INSERT INTO dbo.dim_Employees
        (company_id, floor_id, team_id, first_name, last_name, email, employee_role, hire_date, aad_object_id)
    VALUES
        (1, 1, 1, N'RLS', N'TC5Principal', N'rls.tc5.principal@tcp-capital.ro', 'trader', '2024-01-02', @principal_oid_tc5);
    SET @principal_emp_tc5 = SCOPE_IDENTITY();

    INSERT INTO dbo.dim_Employees
        (company_id, floor_id, team_id, first_name, last_name, email, employee_role, hire_date, aad_object_id)
    VALUES
        (1, 1, 1, N'RLS', N'TC5Other', N'rls.tc5.other@tcp-capital.ro', 'trader', '2024-01-02', @other_oid_tc5);
    SET @other_emp_tc5 = SCOPE_IDENTITY();

    INSERT INTO dbo.dim_Accounts (trader_id, account_code, account_type, currency, opened_on)
    VALUES (@principal_emp_tc5, 'RLS-TC5-PRINCIPAL', 'paper', 'EUR', '2024-01-02');
    SET @principal_acc_tc5 = SCOPE_IDENTITY();

    INSERT INTO dbo.dim_Accounts (trader_id, account_code, account_type, currency, opened_on)
    VALUES (@other_emp_tc5, 'RLS-TC5-OTHER', 'paper', 'EUR', '2024-01-02');
    SET @other_acc_tc5 = SCOPE_IDENTITY();

    -- Grant the principal trader scope tied to their own employee_id.
    INSERT INTO dbo.dim_UserRoles (aad_object_id, employee_id, scope, is_active)
    VALUES (@principal_oid_tc5, @principal_emp_tc5, 'trader', 1);

    DECLARE @mkt_tc5 INT  = (SELECT TOP 1 market_id     FROM dbo.dim_Markets   WHERE symbol = 'AAPL');
    DECLARE @sess_tc5 INT = (SELECT TOP 1 session_id    FROM dbo.dim_Sessions  WHERE session_code = 'regular');
    DECLARE @ord_tc5 INT  = (SELECT TOP 1 order_type_id FROM dbo.dim_OrderType WHERE order_type_code = 'market');

    DECLARE @t_in_tc5  DATETIMEOFFSET(3) = CAST('2024-05-07T10:00:00+02:00' AS DATETIMEOFFSET(3));
    DECLARE @t_out_tc5 DATETIMEOFFSET(3) = CAST('2024-05-07T11:00:00+02:00' AS DATETIMEOFFSET(3));

    -- Activate the principal's trader scope.
    EXEC sp_set_session_context @key=N'aad_object_id', @value=@principal_oid_tc5, @read_only=0;

    DECLARE @block_error_number INT = 0;

    BEGIN TRY
        -- Attempt to insert a row attributed to the OTHER trader -- must be blocked.
        INSERT INTO dbo.fact_Trades
            (trade_uid, trader_id, account_id, market_id, session_id, order_type_id,
             side, quantity, price_entry, price_exit, time_entry, time_exit,
             gross_pnl_eur, commission_eur, net_pnl_eur, is_open)
        VALUES
            ('T20240507-9501', @other_emp_tc5, @other_acc_tc5, @mkt_tc5, @sess_tc5, @ord_tc5,
             'B', 10, 100.000000, 101.000000, @t_in_tc5, @t_out_tc5, 10.0000, 0.5000, 9.5000, 0);
    END TRY
    BEGIN CATCH
        SET @block_error_number = ERROR_NUMBER();
    END CATCH;

    IF @block_error_number = 0
        RAISERROR('TC-5 FAILED: cross-trader INSERT was not blocked by the security policy.', 16, 1);
    ELSE IF @block_error_number <> 33504
        RAISERROR('TC-5 FAILED: expected error 33504, got %d.', 16, 1, @block_error_number);
    ELSE
        PRINT 'TC-5 OK: BLOCK PREDICATE AFTER INSERT rejected the cross-trader insert (error 33504).';

    IF @@TRANCOUNT > 0 ROLLBACK;
    EXEC sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0;
END TRY
BEGIN CATCH
    DECLARE @err_tc5 NVARCHAR(2048) = ERROR_MESSAGE();
    IF @@TRANCOUNT > 0 ROLLBACK;
    EXEC sp_set_session_context @key=N'aad_object_id', @value=NULL, @read_only=0;
    RAISERROR('TC-5 block_insert_other_trader failed: %s', 16, 1, @err_tc5);
END CATCH
GO
