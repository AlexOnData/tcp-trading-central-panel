"""Unit tests for scripts/validate_naming_convention.py.

Covers the four object-type patterns (table, view, procedure, function), the
schema-prefix variants (`foo`, `dbo.foo`, `[dbo].[foo]`, `[foo]`), guard
clauses (`IF NOT EXISTS`), and multi-statement files. The validator script
gates every migration that lands in `db/migrations/`, so this suite is the
last line of defence before a violator reaches the SQL agent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the script as a module without requiring it to be packaged. The
# script lives at scripts/validate_naming_convention.py relative to the
# repository root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "validate_naming_convention.py"

_spec = importlib.util.spec_from_file_location("validate_naming_convention", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules["validate_naming_convention"] = _module
_spec.loader.exec_module(_module)

validate_file = _module.validate_file
main = _module.main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CREATE TABLE
# ---------------------------------------------------------------------------


def test_valid_fact_table_passes(tmp_path: Path) -> None:
    sql = "CREATE TABLE dbo.fact_Trades (id INT);"
    path = _write(tmp_path, "001.sql", sql)
    assert validate_file(path) == []


def test_valid_dim_table_passes(tmp_path: Path) -> None:
    sql = "CREATE TABLE dim_Employees (id INT);"
    path = _write(tmp_path, "002.sql", sql)
    assert validate_file(path) == []


def test_valid_config_table_passes(tmp_path: Path) -> None:
    sql = "CREATE TABLE dbo.config_Capital (id INT);"
    path = _write(tmp_path, "003.sql", sql)
    assert validate_file(path) == []


def test_invalid_table_prefix_fails(tmp_path: Path) -> None:
    sql = "CREATE TABLE dbo.Trades (id INT);"
    path = _write(tmp_path, "004.sql", sql)
    errors = validate_file(path)
    assert len(errors) == 1
    assert "Trades" in errors[0]
    assert "does not match pattern" in errors[0]


def test_lowercase_after_prefix_fails(tmp_path: Path) -> None:
    sql = "CREATE TABLE dim_employees (id INT);"
    path = _write(tmp_path, "005.sql", sql)
    errors = validate_file(path)
    assert len(errors) == 1
    assert "dim_employees" in errors[0]


def test_if_not_exists_is_recognised(tmp_path: Path) -> None:
    sql = "CREATE TABLE IF NOT EXISTS dbo.fact_Trades (id INT);"
    path = _write(tmp_path, "006.sql", sql)
    assert validate_file(path) == []


def test_if_not_exists_violator_is_caught(tmp_path: Path) -> None:
    sql = "CREATE TABLE IF NOT EXISTS dbo.trades (id INT);"
    path = _write(tmp_path, "007.sql", sql)
    errors = validate_file(path)
    assert len(errors) == 1
    assert "trades" in errors[0]


# ---------------------------------------------------------------------------
# Bracketed identifiers (MJ-06)
# ---------------------------------------------------------------------------


def test_bracketed_schema_qualified_table_passes(tmp_path: Path) -> None:
    sql = "CREATE TABLE [dbo].[fact_Trades] (id INT);"
    path = _write(tmp_path, "010.sql", sql)
    assert validate_file(path) == []


def test_bracketed_name_only_table_passes(tmp_path: Path) -> None:
    sql = "CREATE TABLE [dim_Employees] (id INT);"
    path = _write(tmp_path, "011.sql", sql)
    assert validate_file(path) == []


def test_bracketed_lowercase_violator_is_caught(tmp_path: Path) -> None:
    sql = "CREATE TABLE [dbo].[dim_lowerSnake] (id INT);"
    path = _write(tmp_path, "012.sql", sql)
    errors = validate_file(path)
    assert len(errors) == 1
    assert "dim_lowerSnake" in errors[0]


# ---------------------------------------------------------------------------
# CREATE VIEW
# ---------------------------------------------------------------------------


def test_valid_view_passes(tmp_path: Path) -> None:
    sql = "CREATE VIEW dbo.v_trades_enriched AS SELECT 1 AS x;"
    path = _write(tmp_path, "020.sql", sql)
    assert validate_file(path) == []


def test_create_or_alter_view_passes(tmp_path: Path) -> None:
    sql = "CREATE OR ALTER VIEW v_employee_performance AS SELECT 1 AS x;"
    path = _write(tmp_path, "021.sql", sql)
    assert validate_file(path) == []


def test_view_pascal_case_fails(tmp_path: Path) -> None:
    sql = "CREATE VIEW dbo.v_TradesEnriched AS SELECT 1 AS x;"
    path = _write(tmp_path, "022.sql", sql)
    errors = validate_file(path)
    assert len(errors) == 1
    assert "v_TradesEnriched" in errors[0]


# ---------------------------------------------------------------------------
# CREATE PROCEDURE
# ---------------------------------------------------------------------------


def test_valid_procedure_passes(tmp_path: Path) -> None:
    sql = "CREATE PROCEDURE dbo.usp_GenerateDailyTrades AS BEGIN SELECT 1; END"
    path = _write(tmp_path, "030.sql", sql)
    assert validate_file(path) == []


def test_create_or_alter_procedure_passes(tmp_path: Path) -> None:
    sql = "CREATE OR ALTER PROCEDURE usp_ResetSessionContext AS BEGIN SELECT 1; END"
    path = _write(tmp_path, "031.sql", sql)
    assert validate_file(path) == []


def test_procedure_wrong_prefix_fails(tmp_path: Path) -> None:
    sql = "CREATE PROCEDURE dbo.sp_DoSomething AS BEGIN SELECT 1; END"
    path = _write(tmp_path, "032.sql", sql)
    errors = validate_file(path)
    assert len(errors) == 1
    assert "sp_DoSomething" in errors[0]


# ---------------------------------------------------------------------------
# CREATE FUNCTION (MJ-05)
# ---------------------------------------------------------------------------


def test_valid_scalar_function_passes(tmp_path: Path) -> None:
    sql = (
        "CREATE FUNCTION dbo.fn_TradesPredicate (@trader_id INT) "
        "RETURNS TABLE AS RETURN SELECT 1 AS allowed;"
    )
    path = _write(tmp_path, "040.sql", sql)
    assert validate_file(path) == []


def test_valid_tvf_function_passes(tmp_path: Path) -> None:
    sql = (
        "CREATE OR ALTER FUNCTION dbo.tvf_PreviousBusinessDay (@d DATE) "
        "RETURNS TABLE AS RETURN SELECT @d AS d;"
    )
    path = _write(tmp_path, "041.sql", sql)
    assert validate_file(path) == []


def test_function_wrong_prefix_fails(tmp_path: Path) -> None:
    sql = (
        "CREATE FUNCTION dbo.udf_TradesPredicate (@trader_id INT) "
        "RETURNS TABLE AS RETURN SELECT 1 AS allowed;"
    )
    path = _write(tmp_path, "042.sql", sql)
    errors = validate_file(path)
    assert len(errors) == 1
    assert "udf_TradesPredicate" in errors[0]
    assert "fn|tvf" in errors[0]


def test_function_lowercase_after_prefix_fails(tmp_path: Path) -> None:
    sql = "CREATE FUNCTION fn_tradesPredicate () RETURNS INT AS BEGIN RETURN 1; END"
    path = _write(tmp_path, "043.sql", sql)
    errors = validate_file(path)
    assert len(errors) == 1
    assert "fn_tradesPredicate" in errors[0]


# ---------------------------------------------------------------------------
# Multi-statement files
# ---------------------------------------------------------------------------


def test_multi_statement_file_reports_all_violations(tmp_path: Path) -> None:
    sql = """
CREATE TABLE dbo.fact_Trades (id INT);
GO

CREATE TABLE dbo.bad_name (id INT);
GO

CREATE VIEW dbo.v_BadCase AS SELECT 1 AS x;
GO

CREATE PROCEDURE dbo.usp_GoodOne AS BEGIN SELECT 1; END
GO

CREATE FUNCTION dbo.fn_ok () RETURNS INT AS BEGIN RETURN 1; END
GO
"""
    path = _write(tmp_path, "050.sql", sql)
    errors = validate_file(path)
    # Expect 3 violations: bad_name (table), v_BadCase (view), fn_ok (lowercase after fn_).
    assert len(errors) == 3
    joined = " | ".join(errors)
    assert "bad_name" in joined
    assert "v_BadCase" in joined
    assert "fn_ok" in joined


def test_clean_multi_statement_file_passes(tmp_path: Path) -> None:
    sql = """
CREATE TABLE dbo.fact_Trades (id INT);
GO

CREATE TABLE [dbo].[dim_Employees] (id INT);
GO

CREATE OR ALTER VIEW v_trades_enriched AS SELECT 1 AS x;
GO

CREATE PROCEDURE usp_GenerateDailyTrades AS BEGIN SELECT 1; END
GO

CREATE FUNCTION dbo.fn_TradesPredicate (@trader_id INT)
RETURNS TABLE AS RETURN SELECT 1 AS allowed;
GO
"""
    path = _write(tmp_path, "051.sql", sql)
    assert validate_file(path) == []


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "good.sql", "CREATE TABLE dbo.fact_Trades (id INT);")
    monkeypatch.setattr(sys, "argv", ["validate_naming_convention.py", str(tmp_path)])
    assert main() == 0
    assert "All SQL naming conventions are valid." in capsys.readouterr().out


def test_main_returns_one_on_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "bad.sql", "CREATE TABLE dbo.NotPrefixed (id INT);")
    monkeypatch.setattr(sys, "argv", ["validate_naming_convention.py", str(tmp_path)])
    assert main() == 1
    out = capsys.readouterr().out
    assert "NotPrefixed" in out
    assert "does not match pattern" in out


def test_main_warns_on_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(sys, "argv", ["validate_naming_convention.py", str(missing)])
    assert main() == 0
    assert "does not exist" in capsys.readouterr().out


def test_main_returns_one_without_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["validate_naming_convention.py"])
    assert main() == 1
    assert "Usage:" in capsys.readouterr().out


def test_validate_file_reports_read_failure(tmp_path: Path) -> None:
    missing = tmp_path / "ghost.sql"
    errors = validate_file(missing)
    assert len(errors) == 1
    assert "Failed to read file" in errors[0]
