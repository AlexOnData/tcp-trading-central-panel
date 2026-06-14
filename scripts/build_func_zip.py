"""Build a Function App deploy zip respecting .funcignore."""
from __future__ import annotations

import fnmatch
import os
import sys
import zipfile

IGNORE_DIRS = {".venv", "__pycache__", ".pytest_cache", ".mypy_cache", "tests", "docs", ".git", ".github"}
IGNORE_FILE_PATTERNS = ["*.pyc"]


def should_skip_dir(name: str) -> bool:
    return name in IGNORE_DIRS


def should_skip_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in IGNORE_FILE_PATTERNS)


def build(src_dir: str, out_path: str) -> int:
    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if not should_skip_dir(d)]
            for f in files:
                if should_skip_file(f):
                    continue
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, src_dir).replace(os.sep, "/")
                z.write(fp, rel)
                count += 1
    return count


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    out = sys.argv[2] if len(sys.argv) > 2 else "func_deploy.zip"
    n = build(src, out)
    print(f"Zipped {n} files into {out}")
