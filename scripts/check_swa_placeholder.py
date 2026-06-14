"""SWA-config placeholder guard for the pre-commit hook.

Closes the Etapa-11 convergence finding **code11-MJ-01** + **sec11-MN-01**.

Background: `infra/scripts/postprovision.{ps1,sh}` Step 2c substitutes the
`<value-set-by-postprovision>` placeholder in ``swa/staticwebapp.config.json``
with the real SWA-forwarded-secret during ``azd up``. A developer who runs
``azd up`` locally and then immediately ``git commit -a``s would commit the
resolved secret into git history. ``arch10-MJ-03`` documented the footgun;
Etapa-11 wired a pre-commit hook to catch it.

The first cut of the hook read the working tree directly — but a developer
can ``git restore swa/staticwebapp.config.json`` *after* staging the rest of
the changes, which leaves the substituted file in the **index** while the
working tree shows the clean placeholder. The hook would pass; the commit
would still contain the secret.

This script reads the **staged blob** via ``git show :<path>`` so the stage-
vs-restore evasion is closed. The hook fails closed: any error reading the
blob (file missing from the index, git unavailable, decode error) aborts the
commit with operator guidance.

Exit codes:
- 0 — the staged file still contains the literal ``<value-set-by-postprovision>``
  placeholder. Safe to commit.
- 1 — the placeholder is missing from the staged blob (probably substituted
  by postprovision). Operator must `git restore --staged <path>` before
  committing.
- 2 — could not read the staged blob (git error, encoding error). Aborts
  fail-closed.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Final

_STAGED_PATH: Final[str] = "swa/staticwebapp.config.json"
_PLACEHOLDER: Final[str] = "<value-set-by-postprovision>"


def _read_staged_blob(path: str) -> str:
    """Return the contents of ``path`` as it appears in the git index.

    Uses ``git show :<path>`` so the check is robust against the
    stage-then-restore evasion path. Raises ``RuntimeError`` (caught by
    :func:`main`) on any git failure so the hook fails closed.
    """
    try:
        result = subprocess.run(
            ["git", "show", f":{path}"],
            check=True,
            capture_output=True,
            text=False,  # decode below so we control the codec
        )
    except FileNotFoundError as exc:
        msg = f"git not available on PATH: {exc!s}"
        raise RuntimeError(msg) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        msg = f"git show :{path} failed (likely file not staged): {stderr.strip()}"
        raise RuntimeError(msg) from exc

    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"staged blob for {path} is not UTF-8: {exc!s}"
        raise RuntimeError(msg) from exc


def main() -> int:
    """Check that the staged SWA-config still contains the literal placeholder."""
    try:
        staged = _read_staged_blob(_STAGED_PATH)
    except RuntimeError as exc:
        # The file is in the staged set (pre-commit only invokes us when the
        # `files` glob matches), so a missing-from-index error means the
        # commit is genuinely broken. Fail closed.
        print(
            f"ERROR: cannot inspect the staged copy of {_STAGED_PATH}: {exc}",
            file=sys.stderr,
        )
        return 2

    if _PLACEHOLDER not in staged:
        print(
            f"ERROR: the staged copy of {_STAGED_PATH} no longer contains the\n"
            f"       literal `{_PLACEHOLDER}` token. This usually means an\n"
            f"       `azd up` postprovision step substituted the real SWA-forwarded\n"
            f"       secret into the file and `git add -A` then staged the\n"
            f"       substituted version.\n"
            f"\n"
            f"       Fix:  git restore --staged {_STAGED_PATH}\n"
            f"             git restore {_STAGED_PATH}\n"
            f"\n"
            f"       Then re-stage only the changes you intended to commit.\n",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
