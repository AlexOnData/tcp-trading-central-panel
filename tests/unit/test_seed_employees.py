"""Unit tests for tcp.synth.seed_employees — pyodbc fully mocked."""

from __future__ import annotations

import sys
from typing import Any, Final, Iterator

import pytest

# `tcp.synth.__init__.py` runs `from tcp.synth.seed_employees import
# seed_employees`, which rebinds the `seed_employees` attribute on the
# `tcp.synth` package to the *function* — shadowing the submodule attribute.
# `import tcp.synth.seed_employees as seed_module` then resolves to the
# function, not the module, because Python evaluates the dotted form as
# `getattr(getattr(tcp, 'synth'), 'seed_employees')`. Pulling the module
# directly out of `sys.modules` bypasses the shadow (Etapa-10 triage fix).
from tcp.synth.seed_employees import ascii_slug, build_org, seed_employees  # noqa: F401

# After the import above, the actual module object is installed at
# `sys.modules["tcp.synth.seed_employees"]` and is the canonical handle for
# `monkeypatch.setattr` operations.
seed_module = sys.modules["tcp.synth.seed_employees"]

_TEST_GENERATOR_OID: Final[str] = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _patch_generator_oid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin TCP_GENERATOR_OID so seed_employees can set its admin session context."""
    monkeypatch.setenv("TCP_GENERATOR_OID", _TEST_GENERATOR_OID)
    # The bootstrap calls set_admin_session_context(conn, oid) on its owned
    # connection; the FakeConn does not implement sp_set_session_context,
    # so we no-op the helper for unit-test purposes.
    monkeypatch.setattr(seed_module, "set_admin_session_context", lambda conn, oid: None)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_ascii_slug_strips_romanian_diacritics() -> None:
    assert ascii_slug("Răzvan") == "razvan"
    assert ascii_slug("Ștefan") == "stefan"
    assert ascii_slug("Țăndărei") == "tandarei"
    assert ascii_slug("Ana") == "ana"


def test_ascii_slug_handles_spaces_and_apostrophes() -> None:
    assert ascii_slug("Marie d'Aubigne") == "mariedaubigne"


def test_build_org_returns_32_employees() -> None:
    from faker import Faker

    faker = Faker(locale="ro_RO")
    Faker.seed(20260101)
    rows = build_org(faker)
    assert len(rows) == 32
    assert sum(1 for r in rows if r.role == "floor_manager") == 2
    assert sum(1 for r in rows if r.role == "team_lead") == 6
    assert sum(1 for r in rows if r.role == "trader") == 24


def test_build_org_email_format_and_domain() -> None:
    from faker import Faker

    faker = Faker(locale="ro_RO")
    Faker.seed(20260101)
    rows = build_org(faker)
    for r in rows:
        assert r.email.endswith("@tcp-capital.ro")
        local_part = r.email.split("@")[0]
        # Local part must be ASCII-only (no Romanian diacritics).
        assert local_part.encode("ascii", "strict")
        # Single dot separator (suffix variants append digits, not extra dots).
        assert "." in local_part


def test_build_org_unique_emails() -> None:
    from faker import Faker

    faker = Faker(locale="ro_RO")
    Faker.seed(20260101)
    rows = build_org(faker)
    emails = [r.email for r in rows]
    assert len(emails) == len(set(emails))


def test_build_org_is_deterministic_across_runs() -> None:
    from faker import Faker

    faker_a = Faker(locale="ro_RO")
    Faker.seed(20260101)
    rows_a = build_org(faker_a)
    faker_b = Faker(locale="ro_RO")
    Faker.seed(20260101)
    rows_b = build_org(faker_b)
    assert [r.email for r in rows_a] == [r.email for r in rows_b]


# ---------------------------------------------------------------------------
# seed_employees dry-run + mocked DB path
# ---------------------------------------------------------------------------


def test_seed_employees_dry_run_returns_canonical_counts() -> None:
    result = seed_employees(dry_run=True)
    assert result == {
        "companies": 1,
        "floors": 2,
        "teams": 6,
        "employees": 32,
        # 30 = 24 traders + 6 team leads (CR-03 data-engineer review).
        "accounts": 30,
    }


class _FakeCursor:
    """Cursor double that scripts a pre-seeded hierarchy and stable id lookups."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._email_to_id: dict[str, int] = {}
        self._next_id = 100

    def execute(self, sql: str, *params: Any) -> "_FakeCursor":
        self.executed.append((sql, params))
        self._last_sql = sql
        self._last_params = params
        return self

    def fetchone(self) -> Any:
        sql = self._last_sql
        if "dim_Companies" in sql:
            return (1,)
        if "dim_TradingFloors" in sql:
            return (2,)
        if "dim_Teams" in sql:
            return (6,)
        if "dim_Employees" in sql and "SELECT employee_id" in sql:
            email = self._last_params[0]
            if email not in self._email_to_id:
                self._email_to_id[email] = self._next_id
                self._next_id += 1
            return (self._email_to_id[email],)
        return None

    def fetchall(self) -> list[Any]:
        return []

    def close(self) -> None:
        return None


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def patch_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeConn]:
    conn = _FakeConn()
    monkeypatch.setattr(seed_module, "_open_raw_connection", lambda *a, **kw: conn)
    yield conn


def test_seed_employees_commits_once_on_happy_path(patch_db: _FakeConn) -> None:
    result = seed_employees()
    assert result["employees"] == 32
    # 30 = 24 traders + 6 team leads (CR-03 data-engineer review).
    assert result["accounts"] == 30
    assert patch_db.commits == 1
    assert patch_db.rollbacks == 0
    assert patch_db.closed is True


def test_seed_employees_runs_merge_for_every_employee(patch_db: _FakeConn) -> None:
    seed_employees()
    merge_emp = [
        sql for sql, _ in patch_db.cursor_obj.executed if "MERGE dbo.dim_Employees" in sql
    ]
    assert len(merge_emp) == 32


def test_seed_employees_runs_merge_for_every_trading_eligible_account(
    patch_db: _FakeConn,
) -> None:
    seed_employees()
    merge_acc = [
        sql for sql, _ in patch_db.cursor_obj.executed if "MERGE dbo.dim_Accounts" in sql
    ]
    # 24 traders + 6 team leads = 30 accounts.
    assert len(merge_acc) == 30


def test_seed_employees_wires_manager_employee_id(patch_db: _FakeConn) -> None:
    seed_employees()
    updates = [
        params for sql, params in patch_db.cursor_obj.executed if "manager_employee_id" in sql
    ]
    # 2 floor managers are skipped (NULL); 6 team leads + 24 traders = 30 UPDATEs.
    assert len(updates) == 30


def test_seed_employees_idempotent_with_two_calls(patch_db: _FakeConn) -> None:
    seed_employees()
    first = patch_db.commits
    seed_employees()
    assert patch_db.commits == first + 1


def test_seed_employees_rolls_back_on_missing_hierarchy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenCursor(_FakeCursor):
        def fetchone(self) -> Any:
            sql = self._last_sql
            if "dim_Companies" in sql:
                return (0,)
            return super().fetchone()

    class _BrokenConn(_FakeConn):
        def __init__(self) -> None:
            super().__init__()
            self.cursor_obj = _BrokenCursor()

    conn = _BrokenConn()
    monkeypatch.setattr(seed_module, "_open_raw_connection", lambda *a, **kw: conn)
    with pytest.raises(RuntimeError, match="dim_Companies not seeded"):
        seed_employees()
    assert conn.rollbacks == 1
    assert conn.commits == 0
    assert conn.closed is True


def test_seed_employees_email_ascii_normalisation_in_params(patch_db: _FakeConn) -> None:
    seed_employees()
    merge_emp_params = [
        params
        for sql, params in patch_db.cursor_obj.executed
        if "MERGE dbo.dim_Employees" in sql
    ]
    for params in merge_emp_params:
        # email is the 6th positional parameter (0-indexed: 5).
        email = params[5]
        local_part = email.split("@")[0]
        # local_part must encode cleanly as ASCII (no Romanian diacritics).
        local_part.encode("ascii", "strict")
