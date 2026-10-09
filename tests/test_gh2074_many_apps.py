"""gh-2074, gh-2075: a manifest holds several apps, and removes each by name.

The manifest held ONE ``[app]`` table, so a second `jm app` replaced the
first and left its files owned by nothing; and `jm remove` of a component an
app named left the app naming nothing, a tree `status` and `apply` both
refused. Each app is now an ``[[app]]`` row keyed by its name: `jm app`
appends one, `jm remove app <name>` takes one out, and removing what an app
is built from is refused until the app goes.

The #2057 verb gate holds every verb, these included, to the tree `jm apply`
writes (its `apps` and `app-table` shapes). This file holds what its oracles
do not compare:

- **the migration's text.** A schema-8 ``[app]`` table is refused by
  `apply`, naming `jm upgrade`, with the tree untouched; `jm upgrade`
  rewrites the header and nothing else -- a comment above it survives -- and
  is a fixed point on a second run; and after `apply` the WHOLE tree is the
  one a fresh scaffold writes.
- **the wiring `jm remove app` takes back**: the root ``CMakeLists.txt`` and
  ``pyproject.toml``, which no oracle there reads, end byte-identical to the
  project before any app; and a file its author edited is kept, with a note.
- **`jm script`**, which replays every row.

GATE: tests below; each half is proven by `scripts/sabotage.py` in the PR.
"""

from __future__ import annotations

import re
from pathlib import Path

from _jmrun import run_cli, script_round_trip
from just_makeit import _config as C
from just_makeit._upgrade import MIGRATIONS, AppRows

_C = ("--object", "o", "--target", "c", "--flag", "gain:double:1.0:the gain")


def _ok(root: Path, *argv: str) -> str:
    r = run_cli(*argv, cwd=root)
    assert r.returncode == 0, (argv, (r.stdout + r.stderr)[-3000:])
    return r.stdout + r.stderr


def _project(base: Path, *apps: tuple) -> Path:
    base.mkdir(parents=True)
    _ok(base, "new", "p")
    root = base / "p"
    _ok(root, "object", "o")
    for argv in apps:
        _ok(root, "app", *argv)
    return root


def _tree(root: Path) -> "dict[str, bytes]":
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_a_schema_8_app_table_upgrades_in_place(tmp_path):
    old = _project(tmp_path / "old", _C)
    manifest = old / C.FILENAME
    rows = manifest.read_text("utf-8").replace(
        "[[app]]\n", "# the app\n[[app]]\n"
    )
    (schema,) = [n for n, steps in MIGRATIONS.items() if AppRows() in steps]
    table, n = re.subn(r"(?m)^\[\[app\]\]$", "[app]", rows)
    table, m = re.subn(r'(?m)^schema = "\d+"$', f'schema = "{schema}"', table)
    assert (n, m) == (1, 1)
    manifest.write_text(table, "utf-8")

    before = _tree(old)
    r = run_cli("apply", cwd=old)
    assert r.returncode and "`jm upgrade`" in r.stderr, r.stderr
    assert _tree(old) == before, "a refused apply wrote"

    _ok(old, "upgrade")
    assert manifest.read_text("utf-8") == rows
    _ok(old, "upgrade")
    assert manifest.read_text("utf-8") == rows, "not a fixed point"

    _ok(old, "apply")
    fresh = _project(tmp_path / "new", _C)
    ours, theirs = _tree(old), _tree(fresh)
    assert C.load(old) == C.load(fresh)
    del ours[C.FILENAME], theirs[C.FILENAME]
    assert sorted(ours) == sorted(theirs)
    assert [f for f in ours if ours[f] != theirs[f]] == []


def test_remove_app_takes_its_wiring_and_keeps_an_edit(tmp_path):
    root = _project(tmp_path / "x")
    cmake, pyproject = root / "CMakeLists.txt", root / "pyproject.toml"
    pristine = cmake.read_bytes(), pyproject.read_bytes()
    _ok(root, "app", *_C)
    _ok(root, "app", "--object", "o", "--target", "c", "--name", "p2")
    _ok(root, "app", "--object", "o", "--target", "console", "--name", "cli")
    _ok(root, "app", "--object", "o", "--target", "pep723", "--name", "t")
    edited = root / "t.py"
    edited.write_text(edited.read_text("utf-8") + "# mine\n", "utf-8")

    _ok(root, "remove", "app", "p", "--force")
    text = cmake.read_text("utf-8")
    assert "add_executable(p2 " in text and "add_executable(p " not in text
    out = "".join(
        _ok(root, "remove", "app", name, "--force")
        for name in ("p2", "cli", "t")
    )

    assert (cmake.read_bytes(), pyproject.read_bytes()) == pristine
    assert not (root / "native/src/app").exists()
    assert not (root / "src/p/cli.py").exists()
    assert edited.exists() and "t.py holds your edits" in out, out
    assert "app" not in C.load_manifest(root)


def test_script_replays_every_app(tmp_path):
    root = _project(
        tmp_path / "p",
        _C,
        ("--target", "console", "--name", "cli", "--command", "run:run it"),
        ("--object", "o", "--target", "pep723", "--name", "t"),
    )
    before, after = script_round_trip(root, tmp_path / "replay")
    assert len(C.apps(after)) == 3
    assert C.apps(after) == C.apps(before)
