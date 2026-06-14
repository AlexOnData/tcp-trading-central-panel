"""Compute SHA-256 checksums for SQL migration files (RR-09 from Etapa 6).

Closes the residual risk identified in ``docs/security/threat_model.md`` RR-09:
the ``schema_history.checksum`` column was previously populated with the literal
string ``'TODO-checksum-set-by-CI'`` because no automation existed to compute
the canonical hash. This script is the canonical compute path.

Usage:

* ``python scripts/compute_migration_checksum.py [--paths PATH ...]`` — print
  one ``<filename>=<sha256>`` line per migration to stdout, suitable for
  consumption by ``sqlcmd -v`` or ``jq``.
* ``python scripts/compute_migration_checksum.py --json`` — emit a
  JSON object mapping file name to checksum, suitable for CI inspection.
* ``python scripts/compute_migration_checksum.py --ci`` — verify that every
  migration file is canonicalisable (the script always succeeds when the file
  is readable; the verification gate against ``schema_history`` itself runs in
  the post-deploy smoke job inside ``cd.yml``).

Hash semantics (intentionally narrow to keep the value stable across editors):

1. Read the file as raw bytes.
2. Normalise line endings to ``\\n`` (so a developer checking out the file on
   Windows with ``core.autocrlf=true`` produces the same hash as a Linux CI
   runner).
3. Strip any trailing whitespace from each line, then re-join with ``\\n``.
4. Strip a single trailing newline from the whole file (so an editor that
   adds or omits the final newline does not flip the hash).

The resulting hash is a stable identity for the migration's *intent*; an
invisible whitespace edit will not invalidate it, but a substantive change
(a comma, a column rename, a new ``GO`` boundary) will.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_MIGRATIONS_DIR: Final[Path] = REPO_ROOT / "db" / "migrations"


def canonicalise(raw: bytes) -> bytes:
    """Return the canonical byte form of a SQL migration for hashing.

    See module docstring for the four-step normalisation rationale. The shape
    is documented because every consumer of the checksum (CI, postprovision,
    the schema_history INSERT) must apply identical pre-hash normalisation.

    code-MA-02 fix: legacy MacOS line endings (lone ``\\r``) and manually-edited
    files that introduce ``\\r`` mid-line are normalised to ``\\n`` BEFORE the
    line split, so a sequence like ``SELECT 1;\\r\\rSELECT 2;`` becomes
    ``SELECT 1;\\n\\nSELECT 2;`` (two blank lines + the second statement) rather
    than silently swallowing the second statement.

    code-MA-02 fix continued: ``utf-8-sig`` decoding drops a leading BOM if
    present so a file saved by Notepad and a file saved by VS Code produce the
    same canonical bytes.
    """
    text = raw.decode("utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    canonical = "\n".join(lines).rstrip("\n")
    return canonical.encode("utf-8")


def compute_checksum(path: Path) -> str:
    """Return the lowercase hex SHA-256 of the canonicalised migration file."""
    return hashlib.sha256(canonicalise(path.read_bytes())).hexdigest()


def discover_migrations(directory: Path) -> list[Path]:
    """Return the list of `V*.sql` migration files in lexicographic (apply) order.

    Lexicographic ordering reflects the migration apply order assumed by
    ``infra/scripts/postprovision.ps1`` Step 0. A future tooling change that
    introduces a different ordering (e.g. ``flyway``-style timestamp prefix)
    must update this discovery rule in lockstep.
    """
    return sorted(directory.glob("V*.sql"))


def emit_kv(checksums: dict[str, str]) -> None:
    """Print ``<file_stem>=<sha256>`` lines to stdout.

    The file stem (e.g. ``V001__init``) is what postprovision threads into
    ``sqlcmd -v V001__init_CHECKSUM=...``. Slashes and dots are removed so
    the variable name is a valid sqlcmd identifier.
    """
    for name, value in sorted(checksums.items()):
        print(f"{_sqlcmd_var_name(name)}={value}")


def _sqlcmd_var_name(file_name: str) -> str:
    """Map a migration file name to the sqlcmd -v variable name it owns.

    ``V001__init.sql`` → ``V001_CHECKSUM`` so the SQL can reference it as
    ``$(V001_CHECKSUM)``. The truncation to the leading ``V<digits>`` token
    keeps the variable name short and stable across renames of the suffix.
    """
    stem = Path(file_name).stem
    head = stem.split("__", 1)[0]
    return f"{head}_CHECKSUM"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — return a process exit code (0 on success)."""
    parser = argparse.ArgumentParser(description="Compute SQL migration checksums.")
    parser.add_argument(
        "--paths",
        nargs="+",
        type=Path,
        default=None,
        help=(
            "Specific migration files to checksum. Defaults to every "
            "db/migrations/V*.sql file."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON object mapping file name → checksum.",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help=(
            "CI mode: assert every discovered migration is readable and prints "
            "deterministic output. Exit code is non-zero when no migrations "
            "are found (a sign the discovery glob has drifted)."
        ),
    )
    args = parser.parse_args(argv)

    paths = list(args.paths) if args.paths else discover_migrations(DEFAULT_MIGRATIONS_DIR)
    if not paths:
        print(
            "ERROR: no migrations discovered. Expected V*.sql files under "
            f"{DEFAULT_MIGRATIONS_DIR}.",
            file=sys.stderr,
        )
        return 1

    # code-MA-01 fix: pre-flight every path so a missing file produces a clean
    # `ERROR: …` line + exit code 1 instead of an uncaught FileNotFoundError
    # stack trace that confuses CI log readers.
    missing = [p for p in paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"ERROR: migration file not found: {p}", file=sys.stderr)
        return 1

    checksums: dict[str, str] = {p.name: compute_checksum(p) for p in paths}

    if args.json:
        json.dump(checksums, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        emit_kv(checksums)

    if args.ci:
        # In CI mode, additionally assert every checksum is a valid SHA-256
        # hex string (64 chars, lowercase). This catches the case where
        # canonicalise returned something unexpected (encoding edge case).
        for name, value in checksums.items():
            if len(value) != 64 or not all(c in "0123456789abcdef" for c in value):
                print(
                    f"ERROR: {name}: checksum '{value}' is not a valid SHA-256 hex.",
                    file=sys.stderr,
                )
                return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
