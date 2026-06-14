"""Render a migration file with its `__V<n>_CHECKSUM__` placeholder substituted.

Shared by `infra/scripts/postprovision.{ps1,sh}` Step 0 so both platforms
produce byte-identical SQL fed to `sqlcmd`. Without this helper the
PowerShell path used ``Get-Content -Raw`` (preserves CRLF + UTF-8 BOM on
Windows checkouts) while the bash path used Python's universal-newlines
``open(..., encoding="utf-8").read()`` (CRLF→LF and BOM as ``\\ufeff``) —
the two streams diverged byte-for-byte and the RR-09 integrity guarantee
("the SQL applied equals the SQL hashed") was platform-dependent.
See arch-MA-04 in `docs/design/reviews/review_etapa8_cloud_arch.md`.

Output rules (mirror the canonicaliser in ``compute_migration_checksum.py``):

1. Read the file as raw bytes; decode UTF-8 with `utf-8-sig` so a leading BOM
   is dropped consistently regardless of the editor that produced the file.
2. Normalise line endings: ``\\r\\n`` → ``\\n`` and any remaining lone ``\\r``
   → ``\\n`` (matches the canonicaliser's handling of legacy MacOS or
   manually-edited files; see code-MA-02 fix).
3. Replace the placeholder with the supplied checksum value.
4. Emit the rendered text to stdout with no trailing newline added.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def render(path: Path, placeholder: str, checksum: str) -> str:
    """Return the file's contents with ``placeholder`` replaced by ``checksum``."""
    raw = path.read_bytes()
    # `utf-8-sig` strips a leading BOM if present without choking on its absence.
    text = raw.decode("utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace(placeholder, checksum)


def main(argv: list[str] | None = None) -> int:
    """CLI entry — write rendered SQL to stdout. Exit 0 on success."""
    parser = argparse.ArgumentParser(description="Render a migration with checksum substitution.")
    parser.add_argument("--path", required=True, type=Path, help="Migration file to render.")
    parser.add_argument(
        "--placeholder",
        required=True,
        help="Literal placeholder to replace (e.g. __V001_CHECKSUM__).",
    )
    parser.add_argument(
        "--checksum",
        required=True,
        help="SHA-256 hex (64 chars) that will replace the placeholder.",
    )
    args = parser.parse_args(argv)

    if not args.path.is_file():
        print(f"ERROR: migration file not found: {args.path}", file=sys.stderr)
        return 1
    if len(args.checksum) != 64 or not all(c in "0123456789abcdef" for c in args.checksum):
        print(
            f"ERROR: checksum '{args.checksum}' is not a 64-char lowercase SHA-256 hex.",
            file=sys.stderr,
        )
        return 2

    # Write raw UTF-8 bytes via the binary buffer to bypass Python's default
    # stdout text encoding (cp1252 on most Windows installs), which cannot
    # represent characters like U+0219 (Romanian `ș`) present in V001 holiday
    # names. The receiving sqlcmd (invoked by postprovision.ps1) must be told
    # to read UTF-8 via `-f 65001`.
    sys.stdout.buffer.write(render(args.path, args.placeholder, args.checksum).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
