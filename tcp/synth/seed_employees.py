"""One-shot bootstrap of the 32-employee org under TCP Capital Management SRL.

Generates 24 traders + 6 team leads + 2 floor managers using
``Faker(locale='ro_RO')`` with a fixed seed for determinism, and inserts
the rows into ``dim_Employees`` and ``dim_Accounts`` (one live-EUR
account per trading-eligible employee — 24 traders + 6 team leads = 30
accounts, matching the §2.2 "30 trading individuals" contract). Team
leads trade alongside their reports per KPI-LR-001 "Team-Lead Trading
Activity". Idempotent: a second run is a no-op because every INSERT
goes through a ``MERGE ... ON email`` that simply skips already-present
rows.

This is an admin bootstrap path. When the runner opens its own
connection, it sets ``SESSION_CONTEXT('aad_object_id')`` from the
``TCP_GENERATOR_OID`` env var so the V001 RLS predicate can resolve the
principal (the bootstrap MI is registered in ``dim_UserRoles`` with
``scope='admin'``). For test invocations the caller injects ``conn``
with the context already set (see ``tests/integration/test_generator_idempotency.py``).
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Final
from uuid import UUID

import pyodbc
import structlog
from faker import Faker

from tcp.db import _open_raw_connection, set_admin_session_context

_GENERATOR_OID_ENV: Final[str] = "TCP_GENERATOR_OID"

_FAKER_SEED: Final[int] = 20260101
_EMAIL_DOMAIN: Final[str] = "@tcp-capital.ro"

# Hierarchy constants (V001 has already seeded companies/floors/teams; we
# only generate dim_Employees + dim_Accounts here). The IDs match the
# insertion order in V001's seed blocks.
_COMPANY_ID: Final[int] = 1
_FLOORS: Final[tuple[tuple[int, str], ...]] = (
    (1, "București"),
    (2, "Cluj-Napoca"),
)
_TEAMS_BY_FLOOR: Final[dict[int, tuple[int, ...]]] = {
    1: (1, 2, 3),  # BUC-A, BUC-B, BUC-C
    2: (4, 5, 6),  # CLJ-D, CLJ-E, CLJ-F
}

# Hire-date envelope and per-employee offset modulo so the dates are
# spread between 2022-01-01 and 2025-12-31 deterministically.
_HIRE_DATE_FROM: Final[date] = date(2022, 1, 1)
_HIRE_DATE_TO: Final[date] = date(2025, 12, 31)


_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _EmployeeRow:
    """In-memory representation of one row to be MERGEd into dim_Employees."""

    team_id: int
    floor_id: int
    role: str  # 'trader' | 'team_lead' | 'floor_manager'
    first_name: str
    last_name: str
    email: str
    hire_date: date


def _ascii_slug(value: str) -> str:
    """Strip diacritics from a Romanian name to produce an ASCII email slug.

    Uses NFKD decomposition and drops the combining marks so e.g.
    ``Răzvan-Ștefan`` becomes ``razvan-stefan`` — safe to embed in an
    email local-part without RFC violations.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_bytes = decomposed.encode("ascii", "ignore")
    return ascii_bytes.decode("ascii").lower().replace(" ", "").replace("'", "")


def _build_email(first_name: str, last_name: str, taken: set[str]) -> str:
    """Build a unique ``firstname.lastname@tcp-capital.ro`` email.

    If the natural-form local-part collides with an already-issued email
    (e.g. two Ion Popescu employees), a 2-digit numeric suffix is appended
    starting at ``02`` and incrementing until uniqueness is reached.
    """
    base = f"{_ascii_slug(first_name)}.{_ascii_slug(last_name)}"
    candidate = f"{base}{_EMAIL_DOMAIN}"
    suffix = 2
    while candidate in taken:
        candidate = f"{base}{suffix:02d}{_EMAIL_DOMAIN}"
        suffix += 1
    return candidate


def _spread_hire_date(index: int, total: int) -> date:
    """Return a deterministic hire date inside the [2022-01-01, 2025-12-31] envelope."""
    total = max(total, 1)
    span_days = (_HIRE_DATE_TO - _HIRE_DATE_FROM).days
    offset = int(round(span_days * index / max(total - 1, 1))) if total > 1 else 0
    return _HIRE_DATE_FROM.fromordinal(_HIRE_DATE_FROM.toordinal() + offset)


def _build_org(faker: Faker) -> list[_EmployeeRow]:
    """Construct the in-memory employee list in the canonical insertion order.

    Insertion order: floor managers first (so their employee_ids are stable
    when team leads reference them via ``manager_employee_id``), then team
    leads, then traders. This module does not actually wire the
    ``manager_employee_id`` FK — the SQL MERGE side does that lookup —
    but the deterministic ordering matters for tests.
    """
    emails: set[str] = set()
    rows: list[_EmployeeRow] = []

    floor_managers: list[_EmployeeRow] = []
    for floor_id, _city in _FLOORS:
        team_id = _TEAMS_BY_FLOOR[floor_id][0]
        first = faker.first_name()
        last = faker.last_name()
        email = _build_email(first, last, emails)
        emails.add(email)
        floor_managers.append(
            _EmployeeRow(
                team_id=team_id,
                floor_id=floor_id,
                role="floor_manager",
                first_name=first,
                last_name=last,
                email=email,
                hire_date=_HIRE_DATE_FROM,
            )
        )

    team_leads: list[_EmployeeRow] = []
    for floor_id, _city in _FLOORS:
        for team_id in _TEAMS_BY_FLOOR[floor_id]:
            first = faker.first_name()
            last = faker.last_name()
            email = _build_email(first, last, emails)
            emails.add(email)
            team_leads.append(
                _EmployeeRow(
                    team_id=team_id,
                    floor_id=floor_id,
                    role="team_lead",
                    first_name=first,
                    last_name=last,
                    email=email,
                    hire_date=_HIRE_DATE_FROM,
                )
            )

    traders: list[_EmployeeRow] = []
    for floor_id, _city in _FLOORS:
        for team_id in _TEAMS_BY_FLOOR[floor_id]:
            for _ in range(4):
                first = faker.first_name()
                last = faker.last_name()
                email = _build_email(first, last, emails)
                emails.add(email)
                traders.append(
                    _EmployeeRow(
                        team_id=team_id,
                        floor_id=floor_id,
                        role="trader",
                        first_name=first,
                        last_name=last,
                        email=email,
                        hire_date=_HIRE_DATE_FROM,
                    )
                )

    ordered = floor_managers + team_leads + traders
    total = len(ordered)
    # Spread hire dates by their position in the canonical order so the
    # mapping is reproducible.
    rows = [
        _EmployeeRow(
            team_id=r.team_id,
            floor_id=r.floor_id,
            role=r.role,
            first_name=r.first_name,
            last_name=r.last_name,
            email=r.email,
            hire_date=_spread_hire_date(i, total),
        )
        for i, r in enumerate(ordered)
    ]
    return rows


# Public aliases for the pure helpers above. Exposed so unit tests can
# exercise the org-construction and slug contracts without reaching across
# the private/public boundary (python-pro review MA-04).
ascii_slug = _ascii_slug
build_org = _build_org


# ---------------------------------------------------------------------------
# Parameterised SQL statements (safe_query.py-style hygiene)
# ---------------------------------------------------------------------------


_SQL_VERIFY_COMPANIES: Final[str] = (
    "SELECT COUNT(*) FROM dbo.dim_Companies WHERE company_id = ?"
)
_SQL_VERIFY_FLOORS: Final[str] = "SELECT COUNT(*) FROM dbo.dim_TradingFloors"
_SQL_VERIFY_TEAMS: Final[str] = "SELECT COUNT(*) FROM dbo.dim_Teams"

_SQL_MERGE_EMPLOYEE: Final[str] = """
MERGE dbo.dim_Employees AS tgt
USING (SELECT ? AS company_id, ? AS floor_id, ? AS team_id, ? AS first_name,
              ? AS last_name, ? AS email, ? AS employee_role, ? AS hire_date) AS src
ON tgt.email = src.email
WHEN NOT MATCHED THEN
    INSERT (company_id, floor_id, team_id, first_name, last_name, email,
            employee_role, hire_date)
    VALUES (src.company_id, src.floor_id, src.team_id, src.first_name,
            src.last_name, src.email, src.employee_role, src.hire_date);
"""

_SQL_SELECT_EMPLOYEE_ID: Final[str] = (
    "SELECT employee_id FROM dbo.dim_Employees WHERE email = ?"
)

_SQL_UPDATE_MANAGER: Final[str] = (
    "UPDATE dbo.dim_Employees SET manager_employee_id = ? WHERE employee_id = ?"
)

_SQL_MERGE_ACCOUNT: Final[str] = """
MERGE dbo.dim_Accounts AS tgt
USING (SELECT ? AS account_code, ? AS trader_id, ? AS account_type,
              ? AS currency, ? AS opened_on) AS src
ON tgt.account_code = src.account_code
WHEN NOT MATCHED THEN
    INSERT (trader_id, account_code, account_type, currency, opened_on)
    VALUES (src.trader_id, src.account_code, src.account_type,
            src.currency, src.opened_on);
"""


def seed_employees(
    *,
    dry_run: bool = False,
    conn: pyodbc.Connection | None = None,
) -> dict[str, int]:
    """Seed the 32-employee org and one live-EUR account per trading-eligible employee.

    Idempotent: re-running this function against a populated database is a
    no-op because every write goes through ``MERGE ... NOT MATCHED THEN
    INSERT``. Wraps all writes in a single transaction with explicit
    commit/rollback (``pyodbc`` autocommit is OFF in
    ``tcp.db._open_raw_connection``).

    Trading-eligible employees include the 24 traders and the 6 team leads
    (per KPI-LR-001), giving 30 accounts total. The 2 floor managers do
    not receive an account.

    Args:
        dry_run: If ``True``, the function builds the in-memory employee
            list and returns the same counts it would on success, but does
            not open a connection or execute any SQL.
        conn: Optional pre-opened admin connection. When provided, the
            caller owns the transaction (no commit/close here) AND the
            caller is responsible for having set
            ``SESSION_CONTEXT('aad_object_id')`` to an admin principal.
            When ``None`` the function opens its own connection, reads the
            generator MI's OID from ``TCP_GENERATOR_OID``, sets the
            SESSION_CONTEXT, and manages commit/rollback/close.

    Returns:
        A dict ``{'companies', 'floors', 'teams', 'employees', 'accounts'}``
        carrying the expected canonical counts (1, 2, 6, 32, 30).

    Raises:
        RuntimeError: When ``conn is None`` and ``TCP_GENERATOR_OID`` is
            unset or malformed.
    """
    faker = Faker(locale="ro_RO")
    Faker.seed(_FAKER_SEED)
    employees = _build_org(faker)
    # CR-03 (data-engineer review): team leads trade alongside their reports
    # (per 01_BR §2.2 "30 trading individuals" and KPI-LR-001 "Team-Lead
    # Trading Activity"), so they need a live-EUR account too. The runner's
    # _SQL_SELECT_ACTIVE_TRADERS joins on `dim_Accounts` with an INNER JOIN
    # and would silently drop team leads if no account existed.
    trading_eligible = [e for e in employees if e.role in ("trader", "team_lead")]

    expected_counts: dict[str, int] = {
        "companies": 1,
        "floors": len(_FLOORS),
        "teams": sum(len(v) for v in _TEAMS_BY_FLOOR.values()),
        "employees": len(employees),
        "accounts": len(trading_eligible),
    }

    if dry_run:
        _log.info(
            "tcp.synth.seed_employees.dry_run",
            **expected_counts,
        )
        return expected_counts

    owned_conn = conn is None
    generator_oid: UUID | None = None
    if owned_conn:
        raw_oid = os.environ.get(_GENERATOR_OID_ENV)
        if not raw_oid:
            msg = (
                f"{_GENERATOR_OID_ENV} env var is required for the bootstrap "
                "path; set it to the generator MI's AAD object id."
            )
            raise RuntimeError(msg)
        try:
            generator_oid = UUID(raw_oid)
        except (ValueError, TypeError) as exc:
            msg = f"{_GENERATOR_OID_ENV}={raw_oid!r} is not a valid UUID: {exc}"
            raise RuntimeError(msg) from exc
        conn = _open_raw_connection()
    else:
        # The injected connection is expected to carry the SESSION_CONTEXT
        # set by the caller (e.g. the integration-test fixture).
        assert conn is not None
    try:
        if owned_conn and generator_oid is not None:
            set_admin_session_context(conn, generator_oid)
        cursor = conn.cursor()
        try:
            # Verify the pre-seeded hierarchy is in place.
            cursor.execute(_SQL_VERIFY_COMPANIES, _COMPANY_ID)
            row = cursor.fetchone()
            if row is None or int(row[0]) != 1:
                msg = "dim_Companies not seeded; run V001 migrations first."
                raise RuntimeError(msg)
            cursor.execute(_SQL_VERIFY_FLOORS)
            row = cursor.fetchone()
            if row is None or int(row[0]) < len(_FLOORS):
                msg = "dim_TradingFloors not seeded; run V001 migrations first."
                raise RuntimeError(msg)
            cursor.execute(_SQL_VERIFY_TEAMS)
            row = cursor.fetchone()
            if row is None or int(row[0]) < expected_counts["teams"]:
                msg = "dim_Teams not seeded; run V001 migrations first."
                raise RuntimeError(msg)

            # MERGE every employee row.
            for emp in employees:
                cursor.execute(
                    _SQL_MERGE_EMPLOYEE,
                    _COMPANY_ID,
                    emp.floor_id,
                    emp.team_id,
                    emp.first_name,
                    emp.last_name,
                    emp.email,
                    emp.role,
                    emp.hire_date,
                )

            # Resolve employee_ids by email so we can wire the reporting chain.
            email_to_id: dict[str, int] = {}
            for emp in employees:
                cursor.execute(_SQL_SELECT_EMPLOYEE_ID, emp.email)
                row = cursor.fetchone()
                if row is None:
                    msg = f"Employee {emp.email!r} not found after MERGE."
                    raise RuntimeError(msg)
                email_to_id[emp.email] = int(row[0])

            # Wire manager_employee_id:
            #   - floor managers -> NULL
            #   - team leads -> their floor's floor_manager
            #   - traders -> their team's team_lead
            floor_manager_by_floor: dict[int, int] = {
                emp.floor_id: email_to_id[emp.email]
                for emp in employees
                if emp.role == "floor_manager"
            }
            team_lead_by_team: dict[int, int] = {
                emp.team_id: email_to_id[emp.email]
                for emp in employees
                if emp.role == "team_lead"
            }
            for emp in employees:
                if emp.role == "floor_manager":
                    continue
                if emp.role == "team_lead":
                    manager_id = floor_manager_by_floor.get(emp.floor_id)
                else:
                    manager_id = team_lead_by_team.get(emp.team_id)
                if manager_id is None:
                    continue
                cursor.execute(_SQL_UPDATE_MANAGER, manager_id, email_to_id[emp.email])

            # One live-EUR account per trading-eligible employee
            # (24 traders + 6 team leads = 30 accounts).
            for employee in trading_eligible:
                employee_id = email_to_id[employee.email]
                account_code = f"ACC-{employee_id:04d}"
                cursor.execute(
                    _SQL_MERGE_ACCOUNT,
                    account_code,
                    employee_id,
                    "live",
                    "EUR",
                    employee.hire_date,
                )

            if owned_conn:
                conn.commit()
        except Exception:
            if owned_conn:
                try:
                    conn.rollback()
                except pyodbc.Error as exc:
                    _log.warning("tcp.synth.seed_employees.rollback_failed", error=str(exc))
            raise
        finally:
            try:
                cursor.close()
            except pyodbc.Error:
                pass
    finally:
        if owned_conn:
            conn.close()

    _log.info("tcp.synth.seed_employees.complete", **expected_counts)
    return expected_counts
