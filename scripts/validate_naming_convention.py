#!/usr/bin/env python3
"""Validate SQL naming conventions against regex patterns."""

import re
import sys
from pathlib import Path

# Naming convention regex from CLAUDE.md
# fact_*, dim_*, config_* must match ^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$
NAMING_PATTERN = re.compile(r"^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$")

# View naming convention: v_* in snake_case
VIEW_PATTERN = re.compile(r"^v_[a-z][a-z0-9_]*$")

# Stored procedures: usp_* in PascalCase
PROCEDURE_PATTERN = re.compile(r"^usp_[A-Z][a-zA-Z0-9]*$")

# User-defined functions: fn_*Pascal (scalar) or tvf_*Pascal (table-valued).
# Mirrors the usp_* convention; both forms are accepted because Etapa-2
# migrations already include both scalar and table-valued helpers.
FUNCTION_PATTERN = re.compile(r"^(fn|tvf)_[A-Z][a-zA-Z0-9]*$")

# Schema-prefix tolerant identifier capture. Accepts:
#   CREATE TABLE foo
#   CREATE TABLE dbo.foo
#   CREATE TABLE [dbo].[foo]
#   CREATE TABLE [foo]
# Always captures the bare object name (no brackets) in group 1.
_SCHEMA_QUALIFIED = r"(?:\[?dbo\]?\.)?\[?(\w+)\]?"

# Regex to extract object names from CREATE TABLE/VIEW/PROCEDURE/FUNCTION statements.
CREATE_TABLE_PATTERN = re.compile(
    rf"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{_SCHEMA_QUALIFIED}",
    re.IGNORECASE | re.MULTILINE,
)
CREATE_VIEW_PATTERN = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+ALTER\s+)?VIEW\s+{_SCHEMA_QUALIFIED}",
    re.IGNORECASE | re.MULTILINE,
)
CREATE_PROCEDURE_PATTERN = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+{_SCHEMA_QUALIFIED}",
    re.IGNORECASE | re.MULTILINE,
)
CREATE_FUNCTION_PATTERN = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+ALTER\s+)?FUNCTION\s+{_SCHEMA_QUALIFIED}",
    re.IGNORECASE | re.MULTILINE,
)


def validate_file(filepath: Path) -> list[str]:
    """Validate naming conventions in a single SQL file."""
    errors = []

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        return [f"{filepath}: Failed to read file: {e}"]

    # Extract table names
    for match in CREATE_TABLE_PATTERN.finditer(content):
        name = match.group(1)
        if not NAMING_PATTERN.match(name):
            errors.append(
                f"{filepath}: Table '{name}' does not match pattern "
                f"'^(fact|dim|config)_[A-Z][a-zA-Z0-9]*$'"
            )

    # Extract view names
    for match in CREATE_VIEW_PATTERN.finditer(content):
        name = match.group(1)
        if not VIEW_PATTERN.match(name):
            errors.append(
                f"{filepath}: View '{name}' does not match pattern '^v_[a-z][a-z0-9_]*$'"
            )

    # Extract stored procedure names
    for match in CREATE_PROCEDURE_PATTERN.finditer(content):
        name = match.group(1)
        if not PROCEDURE_PATTERN.match(name):
            errors.append(
                f"{filepath}: Procedure '{name}' does not match pattern '^usp_[A-Z][a-zA-Z0-9]*$'"
            )

    # Extract function names. Scalar functions use fn_PascalCase and
    # table-valued functions use tvf_PascalCase.
    for match in CREATE_FUNCTION_PATTERN.finditer(content):
        name = match.group(1)
        if not FUNCTION_PATTERN.match(name):
            errors.append(
                f"{filepath}: Function '{name}' does not match pattern "
                f"'^(fn|tvf)_[A-Z][a-zA-Z0-9]*$'"
            )

    return errors


def main() -> int:
    """Validate all SQL files in the given directories."""
    if len(sys.argv) < 2:
        print("Usage: validate_naming_convention.py <dir1> [<dir2> ...]")
        return 1

    directories = [Path(arg) for arg in sys.argv[1:]]
    all_errors = []

    for directory in directories:
        if not directory.exists():
            print(f"Warning: Directory {directory} does not exist; skipping.")
            continue

        for sql_file in directory.rglob("*.sql"):
            errors = validate_file(sql_file)
            all_errors.extend(errors)

    if all_errors:
        for error in all_errors:
            print(error)
        return 1

    print("All SQL naming conventions are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
