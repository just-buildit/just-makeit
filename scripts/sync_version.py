#!/usr/bin/env python3
"""Sync version field in jb.toml from pyproject.toml.

Run as a pre-commit hook (pass_filenames: false).  Exits 1 when it
modifies jb.toml so pre-commit reports the file as changed and prompts
the user to re-stage it (same convention as ruff --fix and uv-lock).
"""

import re
import sys
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parent.parent
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]

jb_path = root / "jb.toml"
original = jb_path.read_text(encoding="utf-8")
updated = re.sub(
    r'^version\s*=\s*"[^"]*"',
    f'version = "{version}"',
    original,
    flags=re.MULTILINE,
)
if updated != original:
    jb_path.write_text(updated, encoding="utf-8")
    print(f"sync_version: updated jb.toml to {version}")
    sys.exit(1)
