#!/usr/bin/env python3
"""Sync every copy of pyproject.toml's version: bootstrap.toml and uv.lock.

Run as a pre-commit hook (pass_filenames: false).  Exits 1 when it
modifies a file so pre-commit reports the file as changed and prompts
the user to re-stage it (same convention as ruff --fix and uv-lock).

``--exit-zero`` keeps the write and drops that exit code. It exists for
``make bump-version``, where changing the file is the **point** rather than a
finding: a release edits `pyproject.toml`, and if the bump does not carry
`bootstrap.toml` with it, the first `git commit` of every release aborts here
and has to be re-run. That was documented as expected for long enough to be
written into the release runbook, which is what a papercut looks like once it
stops being fixed. Same write either way — the flag answers only "is a change
a failure in this context", which genuinely differs between a gate and a bump.

``uv.lock`` carries the version too, on the project's own ``[[package]]``
entry, and this script writes that one line (gh-1866). Leaving it to
``uv lock`` rewrote the WHOLE file, and every write stamps the lockfile
``revision`` of the uv that made it: uv <= 0.12.21 writes 3, uv >= 0.12.22
writes 5, whatever the lock said before (measured 2026-10-05 on 0.11.28,
0.12.21, 0.12.22 and 0.12.23). So a release cut with a newer uv changed the
revision as well as the version, ``make ci-changes`` read the release commit
as more than a bump, and its CI ran the full matrix. Every one of those uvs
accepts a lock whose version line was written here as up to date, at any
revision, and leaves it alone -- so the bump only asks uv to CHECK
(``uv lock --check``), and the revision stays whatever it was.

Every copy must be found exactly once. A pattern that matches nothing is a
sync that silently stopped syncing, so it is an error (exit 2), not a no-op.

``--root DIR`` points it at a tree other than this repo. Only the test suite
passes it, and it is there so the bump can be exercised for real — against a
throwaway copy of the manifests — instead of by a test that reads this
file and agrees with itself.

Examples
--------
Run from the repository root, as the hook does::

    python scripts/sync_version.py              # exit 1 if it wrote
    python scripts/sync_version.py --exit-zero  # as `make bump-version`
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
project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]
version = project["version"]
# uv keys a lock entry by the normalized name (PEP 503), whatever spelling
# pyproject.toml uses.
lock_name = re.sub(r"[-_.]+", "-", project["name"]).lower()

# Each copy of the version, as (file, the line that carries it). The pattern
# captures what precedes the value and the quote after it, so only the value
# is replaced. bootstrap.toml has one `version =` line, in `[project]`. In
# uv.lock every package has one, and the project's own is the line straight
# after its `name`.
COPIES = (
    ("bootstrap.toml", r'^(version\s*=\s*")[^"]*(")'),
    (
        "uv.lock",
        r'^(name = "' + re.escape(lock_name) + r'"\nversion = ")[^"]*(")$',
    ),
)

changed = False
for name, pattern in COPIES:
    path = root / name
    original = path.read_text(encoding="utf-8")
    updated, n = re.subn(
        pattern, rf"\g<1>{version}\g<2>", original, flags=re.MULTILINE
    )
    if n != 1:
        print(
            f"sync_version: expected one version line in {name}, found {n} "
            f"(pattern {pattern!r})"
        )
        sys.exit(2)
    if updated != original:
        # `\n` on every platform: uv writes the lock with LF, and a CRLF copy
        # would read as a whole-file change.
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(updated)
        print(f"sync_version: updated {name} to {version}")
        changed = True

if changed and "--exit-zero" not in sys.argv:
    sys.exit(1)
