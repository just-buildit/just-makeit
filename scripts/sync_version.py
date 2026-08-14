#!/usr/bin/env python3
"""Sync version field in bootstrap.toml from pyproject.toml.

Run as a pre-commit hook (pass_filenames: false).  Exits 1 when it
modifies bootstrap.toml so pre-commit reports the file as changed and prompts
the user to re-stage it (same convention as ruff --fix and uv-lock).

``--exit-zero`` keeps the write and drops that exit code. It exists for
``make bump-version``, where changing the file is the **point** rather than a
finding: a release edits `pyproject.toml`, and if the bump does not carry
`bootstrap.toml` with it, the first `git commit` of every release aborts here
and has to be re-run. That was documented as expected for long enough to be
written into the release runbook, which is what a papercut looks like once it
stops being fixed. Same write either way — the flag answers only "is a change
a failure in this context", which genuinely differs between a gate and a bump.

``--root DIR`` points it at a tree other than this repo. Only the test suite
passes it, and it is there so the bump can be exercised for real — against a
throwaway copy of the two manifests — instead of by a test that reads this
file and agrees with itself.
"""

import re
import sys
from pathlib import Path

# 3.9/3.10 have no `tomllib`; `tomli` is the backport and is already a runtime
# dependency below 3.11. This script only ever ran in the dev env (3.12) as a
# pre-commit hook, so the bare import was fine — until its test began running
# it under `sys.executable`, which on CI is every version in the matrix. Same
# resolution `_config.py` makes, for the same reason.
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - version-dependent
    import tomli as tomllib

if "--root" in sys.argv:
    root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
else:
    root = Path(__file__).resolve().parent.parent
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]

jb_path = root / "bootstrap.toml"
original = jb_path.read_text(encoding="utf-8")
updated = re.sub(
    r'^version\s*=\s*"[^"]*"',
    f'version = "{version}"',
    original,
    flags=re.MULTILINE,
)
if updated != original:
    jb_path.write_text(updated, encoding="utf-8")
    print(f"sync_version: updated bootstrap.toml to {version}")
    if "--exit-zero" not in sys.argv:
        sys.exit(1)
