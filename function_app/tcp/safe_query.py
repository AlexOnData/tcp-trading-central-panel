"""SQL allowlist validator for the AI assistant.

Treats every input as untrusted. Parses via sqlglot, enforces a strict
allowlist of objects and statement types, rejects anything that could
mutate state, exfiltrate data, or bypass RLS. See:

- ``docs/design/03_architecture.md`` §3.2 (user-question path) and §6.4
  (this module's contract).
- ``docs/decisions/ADR-003-rls-session-context.md`` (the SESSION_CONTEXT
  contract that downstream RLS depends on — this module refuses any SQL
  that could overwrite it).
- ``docs/design/02_database_design.md`` §6 (the allowlisted ``v_*``
  views), §7 (allowlisted read-only procs), §8 (allowlisted UDFs/TVFs).

The contract is **fail-closed**: every doubt becomes a refusal. The
allowlist is the only source of truth for what the LLM may name; any
table, view, function, or procedure not enumerated below is rejected,
even if the SQL would otherwise be syntactically and semantically valid.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Final, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class SafeQueryError(Exception):
    """Base exception raised by :func:`validate` for any rejection.

    Caller code can catch this single type to convert a validation failure
    into an HTTP 422 response; subclasses are available for finer-grained
    telemetry and logging.
    """


class DisallowedStatementError(SafeQueryError):
    """The top-level statement is not a SELECT or wrapping CTE around a SELECT."""


class DisallowedObjectError(SafeQueryError):
    """A referenced table, view, function, or procedure is not allowlisted."""


class DisallowedTokenError(SafeQueryError):
    """A literal token from the deny-list appears anywhere in the input string.

    Caught before sqlglot parsing because some malicious payloads (e.g.,
    ``OPENROWSET(BULK ...)``) fail to parse in the T-SQL dialect and would
    otherwise surface as a generic parse error.
    """


class RowLimitExceededError(SafeQueryError):
    """A TOP/OFFSET-FETCH clause requests more than :data:`MAX_ROW_LIMIT` rows."""


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of a successful :func:`validate` call.

    Attributes:
        sanitized_sql: The re-serialised T-SQL form with ``TOP n`` injected
            or coerced to ``MAX_ROW_LIMIT`` (whichever is smaller). Caller
            code MUST execute this string and not the original input — the
            re-serialised form is what was validated.
        referenced_objects: Lower-cased names of every table/view the SQL
            references, intersected with the allowlists for downstream
            telemetry (``tcp.ask.objects_referenced`` custom dimension).
        estimated_row_limit: The effective row cap that will apply at
            execution time. Either the LLM-supplied value (when ``<=``
            ``MAX_ROW_LIMIT``) or ``MAX_ROW_LIMIT`` itself.
    """

    sanitized_sql: str
    referenced_objects: frozenset[str]
    estimated_row_limit: int


@dataclass(frozen=True)
class ProcCallResult:
    """Outcome of a successful :func:`validate_proc_call`.

    The ``params`` tuple is ordered to match the positional ``?``
    placeholders in ``sql`` exactly, so the caller passes
    ``cursor.execute(result.sql, *result.params)`` without having to
    reconstruct the binding order from a dict. The dedicated container
    closes the parameter-ordering gap surfaced by the Etapa-5 security
    review (MJ-03).

    Attributes:
        sql: Parameterised T-SQL ``EXEC dbo.<proc> @p1 = ?, ...`` string.
        params: Bound values in the same order the placeholders appear in
            ``sql``. Always a tuple so callers cannot mutate it between
            validation and execution.
    """

    sql: str
    params: tuple[Any, ...]


# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------


ALLOWED_VIEWS: Final[frozenset[str]] = frozenset(
    {
        "v_trades_enriched",
        "v_employee_performance",
        "v_team_performance",
        "v_floor_performance",
        "v_daily_pnl",
    }
)
"""Reporting views the AI assistant may select from (``02_DB §6``)."""

ALLOWED_DIMS: Final[frozenset[str]] = frozenset(
    {
        "dim_Companies",
        "dim_TradingFloors",
        "dim_Teams",
        "dim_Employees",
        "dim_Accounts",
        "dim_Markets",
        "dim_Sessions",
        "dim_OrderType",
        "dim_Date",
    }
)
"""Dimension tables the AI assistant may join through (``02_DB §3``).

``dim_UserRoles`` is intentionally **excluded**: it carries RLS scope
metadata (AAD object id → scope mapping) and the assistant must not be
able to enumerate or filter on it.
"""

ALLOWED_PROCS: Final[frozenset[str]] = frozenset(
    {
        "usp_GetEmployeePerformance",
        "usp_GetTopPerformers",
    }
)
"""Read-only stored procedures the assistant may invoke (``02_DB §7.2``, §7.3).

The generator proc ``usp_GenerateDailyTrades`` is intentionally absent —
it writes to ``fact_Trades`` and is only callable from the cron path.
"""

ALLOWED_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        "tvf_GetCapitalBaseline",
        "tvf_RiskMetrics",
        "fn_GetCapitalBaseline",
        "fn_IsTradingDay",
        "fn_PreviousBusinessDay",
    }
)
"""User-defined functions / TVFs the assistant may reference (``02_DB §8``)."""

MAX_ROW_LIMIT: Final[int] = 1000
"""Hard upper bound on rows the assistant can return per query (``03_arch §6.4``)."""


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------


# Allowlist of well-known T-SQL scalar/aggregate functions the LLM may call
# inline without being treated as a "user-defined" lookup. Restricting this
# set keeps the surface narrow without blocking ordinary aggregation queries.
_BUILTIN_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # Aggregates / window
        "avg", "count", "count_big", "sum", "min", "max", "stdev", "stdevp",
        "stddev", "stddev_pop", "variance", "variance_pop",  # sqlglot-canonical aliases
        "var", "varp", "percentile_cont", "percentile_disc", "row_number",
        "rank", "dense_rank", "ntile", "lag", "lead", "first_value",
        "last_value", "cume_dist", "percent_rank",
        # Scalar math / casts
        "abs", "ceiling", "floor", "round", "power", "sqrt", "log", "log10",
        "exp", "sign", "cast", "convert", "try_cast", "try_convert", "iif",
        "choose", "coalesce", "isnull", "nullif", "case",
        # String
        "len", "left", "right", "substring", "lower", "upper", "ltrim",
        "rtrim", "trim", "replace", "concat", "concat_ws", "stuff",
        "format", "string_agg", "patindex", "charindex",
        # Date/time — both T-SQL spellings and sqlglot canonical forms
        "getdate", "sysdatetime", "sysdatetimeoffset", "current_timestamp",
        "datepart", "datename", "datediff", "dateadd", "datefromparts",
        "year", "month", "day", "eomonth", "switchoffset", "todatetimeoffset",
        # sqlglot normalizes some T-SQL functions to standard-SQL canonical
        # names; accept both forms so the validator stays version-tolerant.
        "date_add", "date_diff", "date_from_parts", "extract", "last_day",
        "time_to_str", "time_str_to_time", "ts_or_ds_to_date",
        "currenttimestamp",
        # sqlglot maps T-SQL date functions to these canonical class names:
        #   SYSDATETIMEOFFSET()   -> current_timestamp_l_t_z (CurrentTimestampLTZ)
        #   GETDATE/SYSDATETIME   -> current_timestamp (covered above)
        # Add the LTZ variant and a few defensive aliases for week/quarter/trunc.
        "current_timestamp_l_t_z", "current_timestamp_ltz",
        "date_trunc", "datetrunc", "week", "quarter", "weekday",
        "datetimefromparts", "datetime_from_parts",
        "ws_oracle", "from_days", "to_days",
        "if", "ifnull", "greatest", "least",
        # Misc safe
        "exists", "in", "between", "not",
    }
)


# Token deny-list. Each regex is compiled with re.IGNORECASE and anchored
# with word boundaries (or a custom anchor for non-word characters) so the
# match is intent-revealing rather than incidental ("INTO" matches the
# keyword but not the substring inside ``CATEGORY = 'INTOXICANT'``).
#
# The list is intentionally broader than what sqlglot's parse path could
# detect: payloads like ``OPENROWSET(BULK ...)`` fail to parse in the T-SQL
# dialect, so the deny-list runs **first** to surface a precise reason.
_DENY_TOKEN_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (token, re.compile(pattern, re.IGNORECASE))
    for token, pattern in (
        # Comments first — multi-statement smuggling is the highest-value
        # vector and we want the precise reason in telemetry.
        ("-- comment", r"--"),
        ("/* comment */", r"/\*"),
        ("*/ comment terminator", r"\*/"),
        # System / extended-proc names. These are listed *before* the
        # generic ``EXEC`` keyword so the error message surfaces the more
        # informative cause (e.g., ``sp_OACreate`` rather than ``EXEC``).
        ("sp_set_session_context", r"\bsp_set_session_context\b"),
        ("sp_executesql", r"\bsp_executesql\b"),
        ("sp_OACreate", r"\bsp_OACreate\b"),
        ("sp_OAMethod", r"\bsp_OAMethod\b"),
        ("SESSION_CONTEXT", r"\bSESSION_CONTEXT\b"),
        ("xp_cmdshell", r"\bxp_cmdshell\b"),
        # Generic extended-proc prefix: any identifier starting with ``xp_``
        # is rejected. Belt-and-braces guard against new extended procs.
        ("xp_* extended proc", r"\bxp_[a-zA-Z0-9_]+\b"),
        # External-data sources (listed before the generic ``BULK`` keyword).
        ("OPENROWSET", r"\bOPENROWSET\b"),
        ("OPENDATASOURCE", r"\bOPENDATASOURCE\b"),
        ("OPENQUERY", r"\bOPENQUERY\b"),
        ("OPENXML", r"\bOPENXML\b"),
        # DDL / DML keywords.
        ("INSERT", r"\bINSERT\b"),
        ("UPDATE", r"\bUPDATE\b"),
        ("DELETE", r"\bDELETE\b"),
        ("MERGE", r"\bMERGE\b"),
        ("DROP", r"\bDROP\b"),
        ("CREATE", r"\bCREATE\b"),
        ("ALTER", r"\bALTER\b"),
        ("TRUNCATE", r"\bTRUNCATE\b"),
        ("GRANT", r"\bGRANT\b"),
        ("REVOKE", r"\bREVOKE\b"),
        ("DENY", r"\bDENY\b"),
        # Server-level operations.
        ("BACKUP", r"\bBACKUP\b"),
        ("RESTORE", r"\bRESTORE\b"),
        ("BULK", r"\bBULK\b"),
        ("WAITFOR", r"\bWAITFOR\b"),
        ("UNION", r"\bUNION\b"),
        # INTO catches ``SELECT ... INTO #tmp`` and similar table-copy
        # constructs. Excludes the ``INSERT INTO`` form because the
        # ``INSERT`` token is already denied above; this clause guards
        # against table-copy patterns the LLM might invent without an
        # INSERT keyword.
        ("INTO", r"\bINTO\b"),
        ("SHUTDOWN", r"\bSHUTDOWN\b"),
        ("KILL", r"\bKILL\b"),
        ("DBCC", r"\bDBCC\b"),
        # Generic execute keywords — listed last so the more specific
        # ``sp_*`` and ``xp_*`` names above surface first in the error.
        ("EXEC", r"\bEXEC\b"),
        ("EXECUTE", r"\bEXECUTE\b"),
    )
)


_TOP_RE: Final[re.Pattern[str]] = re.compile(r"\bTOP\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(sql: str) -> ValidationResult:
    """Validate an LLM-emitted SQL string and return a sanitised T-SQL form.

    The function executes the contract documented in
    ``docs/design/03_architecture.md §6.4``:

    1. Strip surrounding whitespace and trailing semicolons.
    2. Reject empty input.
    3. Reject T-SQL comment forms (``--``, ``/* ... */``) — see the
       multiple-statement smuggling note in the module docstring.
    4. Reject any token from :data:`_DENY_TOKEN_PATTERNS` (case-insensitive,
       word-boundary anchored). The deny-list catches payloads that fail to
       parse in T-SQL (e.g., ``OPENROWSET``) before invoking sqlglot.
    5. Parse with ``sqlglot.parse_one(sql, dialect="tsql")``. Parse failure
       → ``SafeQueryError``.
    6. Reject if the parser surfaces more than one top-level statement
       (multi-statement smuggling).
    7. Walk the AST:
        - Top-level must be a ``Select`` (CTEs are unwrapped first).
        - Every referenced table must appear in
          :data:`ALLOWED_VIEWS` ∪ :data:`ALLOWED_DIMS`.
        - Every referenced function must be a built-in or appear in
          :data:`ALLOWED_FUNCTIONS`.
        - ``INTO`` / table-copy constructs and ``UNION`` are denied.
        - Stored-proc calls inside the SELECT are denied — the LLM uses
          :func:`validate_proc_call` for the structured-intent path.
        - CTEs are validated recursively.
    8. Inject or clamp ``TOP n`` to :data:`MAX_ROW_LIMIT`. ``OFFSET ...
       FETCH NEXT n`` larger than the cap raises ``RowLimitExceededError``.
    9. Re-serialise the AST and return :class:`ValidationResult`.

    Args:
        sql: The untrusted SQL string emitted by the LLM. Empty / non-string
            values are rejected.

    Returns:
        A :class:`ValidationResult` with the sanitised SQL, the set of
        allowlisted objects referenced, and the effective row cap.

    Raises:
        SafeQueryError: For empty or non-string inputs.
        DisallowedStatementError: When the statement is not a SELECT (or
            a CTE wrapping a SELECT) or when more than one statement is
            present.
        DisallowedObjectError: When a referenced table, view, function, or
            procedure is not allowlisted.
        DisallowedTokenError: When a literal token from the deny-list
            appears in the input.
        RowLimitExceededError: When ``TOP``/``FETCH NEXT`` exceeds
            :data:`MAX_ROW_LIMIT`.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        msg = "validate(): input is empty"
        raise SafeQueryError(msg)

    _enforce_token_denylist(stripped)
    parsed = _parse_single_statement(stripped)
    inner_select = _unwrap_select(parsed)
    referenced = _walk_and_validate(inner_select)
    effective_limit = _enforce_row_limit(inner_select)

    sanitized = parsed.sql(dialect="tsql")
    return ValidationResult(
        sanitized_sql=sanitized,
        referenced_objects=frozenset(referenced),
        estimated_row_limit=effective_limit,
    )


def validate_proc_call(proc_name: str, params: dict[str, Any]) -> ProcCallResult:
    """Validate a stored-procedure invocation and return SQL + ordered params.

    The assistant may emit a structured-intent envelope of the form
    ``{ "proc": "usp_GetEmployeePerformance", "params": { ... } }`` as an
    alternative to raw SQL. This function checks that the proc name is in
    :data:`ALLOWED_PROCS`, that the parameter set matches the documented
    typed contract (``02_DB §7.2`` / §7.3), and returns a
    :class:`ProcCallResult` whose ``params`` tuple is ordered to match the
    positional ``?`` placeholders in ``sql`` exactly.

    The dedicated return type closes the parameter-ordering gap surfaced by
    the Etapa-5 security review (MJ-03): a caller that builds positional
    args from a dict cannot accidentally swap two same-typed parameters
    (e.g., ``from_date`` and ``to_date``).

    Args:
        proc_name: The exact name of the procedure (case-sensitive — the
            allowlist preserves the documented PascalCase form).
        params: A mapping of parameter name → Python value. Extra keys are
            rejected; missing required keys are rejected.

    Returns:
        A :class:`ProcCallResult` carrying the parameterised T-SQL string
        and the ordered tuple of bound values.

    Raises:
        DisallowedObjectError: When ``proc_name`` is not in
            :data:`ALLOWED_PROCS`.
        SafeQueryError: When the parameter shape does not match the
            documented contract.
    """
    if proc_name not in ALLOWED_PROCS:
        msg = f"validate_proc_call(): proc '{proc_name}' is not in the allowlist"
        raise DisallowedObjectError(msg)

    spec = _PROC_SIGNATURES[proc_name]
    expected = set(spec)
    received = set(params)
    if expected != received:
        missing = sorted(expected - received)
        unexpected = sorted(received - expected)
        msg = (
            f"validate_proc_call(): parameter shape mismatch for {proc_name} — "
            f"missing={missing}, unexpected={unexpected}"
        )
        raise SafeQueryError(msg)

    for key, expected_type in spec.items():
        value = params[key]
        if not isinstance(value, expected_type):
            msg = (
                f"validate_proc_call(): parameter {key!r} for {proc_name} must be "
                f"{expected_type.__name__}, got {type(value).__name__}"
            )
            raise SafeQueryError(msg)

    # Translate Python-facing param names to their actual SQL counterparts
    # (e.g. `from_date` → `from`). Etapa-10 code10-MJ-01 fix: without this,
    # SQL Server rejects EXEC with Msg 8145.
    placeholders = ", ".join(
        f"@{_PROC_PARAM_TO_SQL.get(k, k)} = ?" for k in spec
    )
    ordered_values = tuple(params[k] for k in spec)
    return ProcCallResult(
        sql=f"EXEC dbo.{proc_name} {placeholders}",
        params=ordered_values,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# Stored-proc signatures. Dict keys are the **Python-facing** parameter names
# (used by the Anthropic tool schema and the test suite); `_PROC_PARAM_TO_SQL`
# below maps each Python name to the actual `@<name>` parameter declared in
# V001 — `from`/`to` are reserved words in T-SQL grammar (and clash with
# Python's `from` keyword if used as identifiers), so the public surface uses
# the safer `from_date`/`to_date` aliases.
_PROC_SIGNATURES: Final[dict[str, dict[str, type]]] = {
    "usp_GetEmployeePerformance": {
        "employee_id": int,
        "from_date": str,  # ISO yyyy-mm-dd; caller converts to date upstream.
        "to_date": str,
    },
    "usp_GetTopPerformers": {
        "scope": str,
        "from_date": str,
        "to_date": str,
        "top_n": int,
    },
}

# Etapa-10 code10-MJ-01 fix: V001's procs declare `@from DATE`, `@to DATE` —
# the Python-facing `from_date` / `to_date` aliases above must be translated
# back to the SQL parameter names at render time, or SQL Server rejects the
# EXEC with Msg 8145 ("Procedure or function has too many arguments
# specified"). Unmapped keys pass through unchanged.
_PROC_PARAM_TO_SQL: Final[dict[str, str]] = {
    "from_date": "from",
    "to_date": "to",
}


_STRING_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r"N?'(?:''|[^'])*'",
    re.DOTALL,
)
"""Match T-SQL string literals (regular and N-prefixed).

Used by :func:`_normalise_for_denylist_scan` to strip literal content
before the keyword scan so words like ``INTO`` or ``UNION`` inside a
``WHERE name LIKE '%INTO%'`` clause do not fire a false-positive token
rejection (ai MA-03).
"""


def _normalise_for_denylist_scan(sql: str) -> str:
    """Return ``sql`` with Unicode normalised and string literals masked.

    Two transformations:

    - **NFKC normalisation** collapses look-alike Unicode forms (e.g., a
      mathematical hyphen vs an ASCII ``-``) so a payload like
      ``SELECT 1 ‐‐ DROP ...`` cannot smuggle a ``--`` comment
      past the literal-character regex (ai MA-01).
    - **String-literal masking** replaces every ``'...'`` (or ``N'...'``)
      literal with a same-length filler that is guaranteed not to match
      any deny-list pattern, so a substring like ``'INTO'`` inside a
      ``WHERE`` clause does not fire the ``INTO`` token rejection
      (ai MA-03).

    The normalised string is **only** used to drive the deny-list scan —
    the original ``sql`` is what sqlglot later parses, so any masking
    here cannot weaken the AST-walk allowlist.
    """
    normalised = unicodedata.normalize("NFKC", sql)
    # Reject zero-width / format control characters outright before any
    # other check — they have no place in machine-emitted SQL and would
    # let an attacker desync the regex anchor from the visible token.
    for ch in normalised:
        if unicodedata.category(ch) in {"Cf", "Cc"} and ch not in {"\t", "\n", "\r"}:
            msg = (
                f"disallowed control character U+{ord(ch):04X} in input"
            )
            raise DisallowedTokenError(msg)
    return _STRING_LITERAL_RE.sub(lambda m: " " * len(m.group(0)), normalised)


def _enforce_token_denylist(sql: str) -> None:
    """Raise :class:`DisallowedTokenError` if any deny-list pattern matches.

    Runs before sqlglot to surface a precise reason for malformed-but-malicious
    payloads (e.g., ``OPENROWSET(BULK ...)``) that fail to parse in the
    T-SQL dialect.

    The scan operates on a Unicode-normalised, literal-masked copy of the
    input (see :func:`_normalise_for_denylist_scan`) so the comment-
    smuggling bypass surfaced by Etapa-5 ai MA-01 and the string-literal
    false-positive surfaced by ai MA-03 are both closed.
    """
    scan_target = _normalise_for_denylist_scan(sql)
    for token, pattern in _DENY_TOKEN_PATTERNS:
        if pattern.search(scan_target):
            msg = f"disallowed token detected: {token}"
            raise DisallowedTokenError(msg)


def _parse_single_statement(sql: str) -> exp.Expression:
    """Parse ``sql`` and ensure it contains exactly one top-level statement.

    ``sqlglot.parse`` returns a list; the deny-list above already rejects
    SQL comments (the easiest smuggling form), but a literal ``;`` is
    permitted *inside* a single trailing-statement parse and the function
    must still reject ``SELECT 1; SELECT 2``.
    """
    try:
        statements = sqlglot.parse(sql, dialect="tsql")
    except ParseError as exc:
        msg = f"sqlglot parse error: {exc}"
        raise SafeQueryError(msg) from exc

    # ``sqlglot.parse`` may yield a trailing ``None`` for an empty fragment
    # after a final semicolon; filter those before counting.
    non_empty = [s for s in statements if s is not None]
    if not non_empty:
        msg = "no parseable statement"
        raise SafeQueryError(msg)
    if len(non_empty) > 1:
        msg = "multiple statements are not allowed"
        raise DisallowedStatementError(msg)
    # sqlglot.parse declares its element type as ``Expr`` in some
    # releases; the cast pins the public ``Expression`` interface for
    # downstream callers (``Expr`` is currently an alias).
    return cast(exp.Expression, non_empty[0])


def _unwrap_select(node: exp.Expression) -> exp.Select:
    """Return the innermost SELECT, rejecting non-SELECT top-level statements.

    A bare ``Select`` is returned as-is. A ``Subquery``/``Paren`` wrapper
    is unwrapped. Anything else (``Insert``, ``Update``, ``Delete``,
    ``Merge``, ``Create``, ``Drop``, ``Alter``, ``Union``, ``Command``...)
    raises :class:`DisallowedStatementError`.
    """
    if isinstance(node, exp.Select):
        return node
    if isinstance(node, exp.Subquery) and isinstance(node.this, exp.Select):
        return node.this
    if isinstance(node, exp.Paren) and isinstance(node.this, exp.Select):
        return node.this
    msg = (
        f"top-level statement must be SELECT, got {type(node).__name__}"
    )
    raise DisallowedStatementError(msg)


def _walk_and_validate(select: exp.Select) -> set[str]:
    """Recursively check every node in ``select`` against the allowlists.

    Returns the lower-cased names of the allowlisted tables/views the
    expression references, for telemetry attribution. CTEs are visited
    eagerly so an inner SELECT inside ``WITH cte AS (SELECT ...)`` is
    subject to the same checks as the outer statement.
    """
    referenced: set[str] = set()
    cte_names: set[str] = set()

    # CTE definitions live in ``with_`` for sqlglot 30+; older releases used
    # the ``with`` key. The ``find_all`` walk picks them up either way.
    for cte in select.find_all(exp.CTE):
        cte_names.add(cte.alias_or_name.lower())
        inner = cte.this
        if not isinstance(inner, exp.Select):
            msg = "CTE body must be a SELECT"
            raise DisallowedStatementError(msg)
        # Recurse into the CTE's inner SELECT to apply the same checks.
        inner_refs = _walk_and_validate(inner)
        referenced.update(inner_refs)
        # ai MA-04: enforce the row cap on every CTE body too. An outer
        # ``SELECT TOP 10 *`` cannot protect against a CTE that
        # materialises an unbounded inner SELECT into tempdb. Either the
        # inner has its own ``TOP n <= MAX_ROW_LIMIT`` clause (preserved
        # as-is) or we inject ``TOP MAX_ROW_LIMIT`` ourselves.
        _enforce_row_limit(inner)

    # Reject UNION / EXCEPT / INTERSECT subtrees explicitly. The deny-list
    # already catches the ``UNION`` keyword, but EXCEPT/INTERSECT slip
    # through the token list and we want a precise error if the LLM emits
    # them.
    for set_op in select.find_all(exp.Union, exp.Except, exp.Intersect):
        msg = (
            f"set operations are not allowed (found {type(set_op).__name__})"
        )
        raise DisallowedStatementError(msg)

    # Reject any nested DML / DDL that sqlglot may surface as a subquery
    # (defence in depth — the deny-list above already catches the keywords).
    for forbidden in select.find_all(
        exp.Insert, exp.Update, exp.Delete, exp.Merge,
        exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Anonymous,
    ):
        # ``Anonymous`` shows up for unknown function-like calls; we'll
        # validate those in the function-name pass instead of failing here.
        if isinstance(forbidden, exp.Anonymous):
            continue
        msg = (
            f"nested {type(forbidden).__name__} statement is not allowed"
        )
        raise DisallowedStatementError(msg)

    # ``SELECT ... INTO`` smuggles a CREATE TABLE-equivalent into a SELECT;
    # the deny-list catches the ``INTO`` keyword, but the AST also exposes
    # it as ``select.args["into"]`` which we surface as a precise error.
    if select.args.get("into") is not None:
        msg = "SELECT ... INTO is not allowed"
        raise DisallowedStatementError(msg)

    # Table allowlist
    for table in select.find_all(exp.Table):
        name = table.name
        # Schema-qualified or catalog-qualified references must use the
        # default ``dbo`` schema (or be unqualified). Anything else (e.g.,
        # ``master..sys.objects``) means the LLM is reaching outside the
        # allowlisted surface.
        catalog = table.catalog
        schema = table.db
        if catalog and catalog.lower() not in {"", "tcp", "tcp_dev"}:
            msg = f"cross-database reference is not allowed: {catalog}.{schema}.{name}"
            raise DisallowedObjectError(msg)
        if schema and schema.lower() not in {"", "dbo"}:
            msg = f"non-dbo schema reference is not allowed: {schema}.{name}"
            raise DisallowedObjectError(msg)

        # CTE self-references are allowed without further checks.
        if name.lower() in cte_names:
            continue

        if name in ALLOWED_VIEWS or name in ALLOWED_DIMS:
            referenced.add(name)
            continue

        # Case-insensitive fallback for dim_* spellings.
        if name.lower() in {v.lower() for v in ALLOWED_DIMS}:
            # Re-map to the canonical PascalCase name for the telemetry set.
            canonical = next(d for d in ALLOWED_DIMS if d.lower() == name.lower())
            referenced.add(canonical)
            continue
        if name.lower() in {v.lower() for v in ALLOWED_VIEWS}:
            canonical = next(v for v in ALLOWED_VIEWS if v.lower() == name.lower())
            referenced.add(canonical)
            continue

        msg = f"table or view '{name}' is not in the allowlist"
        raise DisallowedObjectError(msg)

    # Function allowlist — two passes to avoid double-walking the AST.
    #
    # ``Anonymous`` subclasses ``Func`` in sqlglot's class hierarchy, so the
    # earlier ``find_all(exp.Anonymous, exp.Func)`` visited each anonymous
    # node twice (py MJ-02). Splitting the walk makes the intent explicit
    # and lets us apply different validation rules to user-defined vs
    # built-in calls.

    # Pass 1: anonymous (unknown / user-defined) calls must be in
    # ALLOWED_FUNCTIONS, and must NOT collide with a proc name — a proc
    # invocation hidden inside a SELECT (e.g., ``SELECT dbo.usp_Foo(...)``)
    # must go through validate_proc_call, never this path (ai MA-02).
    _allowed_funcs_lower = {n.lower() for n in ALLOWED_FUNCTIONS}
    _allowed_procs_lower = {n.lower() for n in ALLOWED_PROCS}
    for anon in select.find_all(exp.Anonymous):
        fn_name = anon.name
        fn_name_lower = fn_name.lower()
        if fn_name_lower in _allowed_procs_lower:
            msg = (
                f"procedure '{fn_name}' must be invoked via validate_proc_call, "
                f"not as an inline function"
            )
            raise DisallowedObjectError(msg)
        if fn_name not in ALLOWED_FUNCTIONS and fn_name_lower not in _allowed_funcs_lower:
            msg = f"function '{fn_name}' is not in the allowlist"
            raise DisallowedObjectError(msg)
        canonical_udf = next(
            (n for n in ALLOWED_FUNCTIONS if n.lower() == fn_name_lower),
            fn_name,
        )
        referenced.add(canonical_udf)

    # Pass 2: recognised built-in functions. Skip Anonymous nodes (already
    # validated above) — they are also instances of exp.Func because of the
    # sqlglot class hierarchy.
    #
    # ALSO skip boolean / unary-operator AST nodes that sqlglot models as
    # `exp.Func` subclasses purely for AST uniformity: `exp.And`, `exp.Or`,
    # `exp.Not`, `exp.Xor`, `exp.Is`, `exp.In`. These are SQL operators, not
    # function calls — gating them as functions falsely rejects every
    # `WHERE x AND y` query the assistant produces.
    _BOOLEAN_OP_NODES = (
        exp.And, exp.Or, exp.Not, exp.Xor, exp.Is, exp.In,
        exp.Paren, exp.Cast, exp.Case, exp.If, exp.Coalesce,
    )
    for func in select.find_all(exp.Func):
        if isinstance(func, exp.Anonymous):
            continue
        if isinstance(func, _BOOLEAN_OP_NODES):
            # Operator-like nodes; not user-supplied identifiers.
            continue
        try:
            canonical = func.sql_name().lower()
        except (AttributeError, TypeError):
            canonical = type(func).__name__.lower()
        # sqlglot emits canonical names with underscores (e.g. DATE_FROM_PARTS)
        # while the project's `_BUILTIN_FUNCTIONS` lists the bare T-SQL spelling
        # (e.g. `datefromparts`). Compare both forms.
        canonical_compact = canonical.replace("_", "")
        if canonical not in _BUILTIN_FUNCTIONS and canonical_compact not in _BUILTIN_FUNCTIONS:
            # Some sqlglot dialects emit user-defined names through typed
            # ``Func`` subclasses (rare). Treat any match against the
            # ALLOWED_FUNCTIONS allowlist as a hit; everything else fails
            # the fail-closed default.
            if canonical in _allowed_funcs_lower:
                referenced.add(next(n for n in ALLOWED_FUNCTIONS if n.lower() == canonical))
                continue
            msg = f"function '{canonical}' is not allowlisted"
            raise DisallowedObjectError(msg)

    return referenced


def _enforce_row_limit(select: exp.Select) -> int:
    """Inject ``TOP MAX_ROW_LIMIT`` if absent; raise if a higher limit is set.

    Handles both the ``SELECT TOP n ...`` form and the ``OFFSET ... FETCH
    NEXT n ROWS ONLY`` form. When neither is present, mutates ``select``
    in place to add a ``TOP MAX_ROW_LIMIT`` clause.
    """
    limit_node = select.args.get("limit")
    if limit_node is not None:
        # ``Limit`` represents ``TOP n``; ``Fetch`` represents the
        # ``OFFSET ... FETCH NEXT n`` form.
        if isinstance(limit_node, exp.Limit):
            literal = limit_node.expression
            n = _literal_int(literal)
        elif isinstance(limit_node, exp.Fetch):
            literal = limit_node.args.get("count")
            n = _literal_int(literal)
        else:
            msg = f"unsupported limit node type: {type(limit_node).__name__}"
            raise SafeQueryError(msg)

        if n is None:
            msg = "could not determine row-limit literal"
            raise SafeQueryError(msg)
        if n > MAX_ROW_LIMIT:
            msg = (
                f"row limit {n} exceeds MAX_ROW_LIMIT={MAX_ROW_LIMIT}"
            )
            raise RowLimitExceededError(msg)
        return n

    # No limit clause: inject ``TOP MAX_ROW_LIMIT``. sqlglot's Select.limit()
    # helper builds the appropriate node and re-serialises as ``TOP n`` for
    # the T-SQL dialect at .sql() time.
    select.set(
        "limit",
        exp.Limit(expression=exp.Literal.number(MAX_ROW_LIMIT)),
    )
    return MAX_ROW_LIMIT


def _literal_int(node: exp.Expression | None) -> int | None:
    """Extract an integer literal from ``node`` or return ``None`` if non-literal."""
    if node is None:
        return None
    if isinstance(node, exp.Literal):
        try:
            return int(node.this)
        except (ValueError, TypeError):
            return None
    return None
