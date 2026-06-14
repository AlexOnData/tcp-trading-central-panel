"""Unit tests for `scripts/compute_migration_checksum.py` (RR-09)."""

from __future__ import annotations

import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "compute_migration_checksum.py"


@pytest.fixture(scope="module")
def helper():
    """Load the compute_migration_checksum module from its file path.

    The script lives under `scripts/` (no `__init__.py`), so it cannot be
    imported via the normal package machinery. ``importlib.util`` loads it
    by path and registers it in `sys.modules` for the test session.
    """
    spec = importlib.util.spec_from_file_location("compute_migration_checksum", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["compute_migration_checksum"] = module
    spec.loader.exec_module(module)
    return module


def test_canonicalise_strips_crlf(helper) -> None:
    """CRLF line endings hash identically to LF endings."""
    crlf = b"line1\r\nline2\r\nline3\r\n"
    lf = b"line1\nline2\nline3\n"
    assert helper.canonicalise(crlf) == helper.canonicalise(lf)


def test_canonicalise_strips_trailing_whitespace(helper) -> None:
    """Trailing spaces/tabs do not change the hash."""
    spaced = b"SELECT 1;   \nSELECT 2;\t\n"
    clean = b"SELECT 1;\nSELECT 2;\n"
    assert helper.canonicalise(spaced) == helper.canonicalise(clean)


def test_canonicalise_strips_final_newline(helper) -> None:
    """Optional trailing newline does not flip the hash."""
    with_nl = b"SELECT 1;\n"
    without_nl = b"SELECT 1;"
    assert helper.canonicalise(with_nl) == helper.canonicalise(without_nl)


def test_canonicalise_substantive_change_flips_hash(helper) -> None:
    """A real semantic change must produce a different canonical bytes."""
    original = b"SELECT 1;\n"
    edited = b"SELECT 2;\n"
    assert helper.canonicalise(original) != helper.canonicalise(edited)


def test_compute_checksum_returns_64_hex(helper, tmp_path: Path) -> None:
    """Helper returns a lowercase 64-char hex SHA-256."""
    f = tmp_path / "V999__demo.sql"
    f.write_text("SELECT 1;\n", encoding="utf-8")
    digest = helper.compute_checksum(f)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_sqlcmd_var_name_strips_suffix(helper) -> None:
    """V001__init.sql → V001_CHECKSUM."""
    assert helper._sqlcmd_var_name("V001__init.sql") == "V001_CHECKSUM"
    assert helper._sqlcmd_var_name("V042__add_index.sql") == "V042_CHECKSUM"


def test_discover_migrations_against_real_dir(helper) -> None:
    """The discovery glob must surface at least V001 + V002 in repo state."""
    found = [p.name for p in helper.discover_migrations(REPO_ROOT / "db" / "migrations")]
    assert "V001__init.sql" in found
    assert "V002__synth_logic.sql" in found


def test_main_kv_output_matches_compute(helper, capsys, tmp_path: Path) -> None:
    """`main` with no flags emits `<VAR>=<sha256>` for each migration."""
    f = tmp_path / "V123__demo.sql"
    f.write_text("CREATE TABLE foo(id INT);\n", encoding="utf-8")
    rc = helper.main(["--paths", str(f)])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.startswith("V123_CHECKSUM=")
    expected = helper.compute_checksum(f)
    assert expected in captured.out


def test_main_json_output_is_parseable(helper, capsys, tmp_path: Path) -> None:
    """--json emits a valid JSON object keyed by file name."""
    f1 = tmp_path / "V001__a.sql"
    f2 = tmp_path / "V002__b.sql"
    f1.write_text("SELECT 1;\n", encoding="utf-8")
    f2.write_text("SELECT 2;\n", encoding="utf-8")
    rc = helper.main(["--json", "--paths", str(f1), str(f2)])
    captured = capsys.readouterr()
    assert rc == 0
    parsed = json.loads(captured.out)
    assert set(parsed.keys()) == {"V001__a.sql", "V002__b.sql"}
    for value in parsed.values():
        assert len(value) == 64


def test_main_ci_mode_returns_zero_for_real_repo(helper) -> None:
    """`--ci` exits 0 against the real db/migrations directory."""
    # capsys cannot replace `sys.stdout` for a pure ``print`` call without
    # full pytest capture; redirect stdout manually to keep the test silent.
    saved = sys.stdout
    try:
        sys.stdout = StringIO()
        rc = helper.main(["--ci"])
    finally:
        sys.stdout = saved
    assert rc == 0


def test_main_fails_cleanly_on_missing_file(helper, tmp_path: Path, capsys) -> None:
    """Missing file produces clean stderr message + exit code 1 (code-MA-01)."""
    missing = tmp_path / "nonexistent.sql"
    rc = helper.main(["--paths", str(missing)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "ERROR: migration file not found" in captured.err
    assert str(missing) in captured.err


def test_main_returns_error_on_empty_default_dir(helper, tmp_path: Path, monkeypatch) -> None:
    """When the discovery dir is empty, exit code is 1 (drift detector)."""
    monkeypatch.setattr(helper, "DEFAULT_MIGRATIONS_DIR", tmp_path)
    rc = helper.main([])
    assert rc == 1


def test_canonicalise_normalises_lone_cr(helper) -> None:
    """code-MA-02: lone `\\r` characters are normalised to `\\n`, not swallowed."""
    # `SELECT 1;\r\rSELECT 2;` previously became `SELECT 1;` because `split("\n")`
    # never saw the `\r` boundaries and the trailing-newline strip then ate the
    # entire suffix. The fix normalises lone `\r` to `\n` before the line split.
    legacy_mac = b"SELECT 1;\r\rSELECT 2;\r"
    canonical = helper.canonicalise(legacy_mac)
    # We expect both statements to survive, separated by an empty line.
    assert b"SELECT 1;" in canonical
    assert b"SELECT 2;" in canonical


def test_canonicalise_drops_utf8_bom(helper) -> None:
    """code-MA-02: a leading UTF-8 BOM produces the same hash as a BOM-less file."""
    bom = b"\xef\xbb\xbf"
    with_bom = bom + b"SELECT 1;\n"
    without_bom = b"SELECT 1;\n"
    assert helper.canonicalise(with_bom) == helper.canonicalise(without_bom)
