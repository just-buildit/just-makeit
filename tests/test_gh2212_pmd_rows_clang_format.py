"""gh-2212: a binding's method table is read whole in a formatted layout.

`_stubs.method_table_flags` tells a stub which members take no keywords,
row by row. A row it failed to match would leave that member unmarked,
silently. The formatter is the adversary in this repo: a project's
clang-format splits a row over lines, puts a space inside its brace and
spaces a cast into ``(PyCFunction)(void (*) (void))``.

This builds the object gate's projects -- standalone, serializable, module,
view and a module of functions -- and `_csym_fixtures`' kind modules (a
composer, whose rows carry the function-pointer cast),
regenerates every binding through the project's own clang-format in GNU
style at 79 columns (doppler's house style), and requires:

- the fixture actually produced that layout;
- every named row of every table is read (counted independently of the
  reader);
- every stub still agrees with its row.

Lives on the PROJECT_ENV_TESTS path because it needs the pinned
clang-format (gh-1442).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import _csym_fixtures as F  # noqa: E402
from _jmrun import run_cli  # noqa: E402
from test_gh2212_object_stub_slash import (  # noqa: E402
    _ROWS,
    _functions_project,
    _stubs,
    _table_bodies,
    _tables,
    unread_rows,
)

_GNU = "BasedOnStyle: GNU\nColumnLimit: 79\nReflowComments: true\n"


def _format_through_clang(root: Path) -> None:
    """Declare the project's clang-format, then regenerate every binding
    through it: the glue is reformatted as jm writes it."""
    (root / ".clang-format").write_text(_GNU)
    cfg = root / "just-makeit.toml"
    cfg.write_text(
        cfg.read_text().replace(
            "[project]", '[project]\nc_style = "clang-format"', 1
        )
    )
    for c in root.rglob("native/src/**/*_ext*.c"):
        c.unlink()
    r = run_cli("apply", cwd=root)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.fixture(scope="module")
def roots(tmp_path_factory):
    where = tmp_path_factory.mktemp("gh2212fmt")
    built = F.build(where, projects={k: F.PROJECTS[k] for k in _ROWS})
    built["fn"] = _functions_project(where)
    for root in built.values():
        _format_through_clang(root)
    return built


@pytest.fixture(scope="module")
def kinds(tmp_path_factory):
    """The kind modules, formatted the same way. Read for their rows only:
    the composer's source type shares a class name with the object it
    composes, which the per-class stub comparison would conflate."""
    where = tmp_path_factory.mktemp("gh2212fmtkinds")
    root = F.build(where, projects={"kinds": F.PROJECTS["kinds"]})["kinds"]
    _format_through_clang(root)
    return root


def _c_files(roots) -> "list[str]":
    return [
        c.read_text(encoding="utf-8")
        for root in roots.values()
        for c in root.rglob("native/src/**/*.c")
    ]


def test_the_fixture_is_formatted(roots, kinds):
    """Armed: the rows are in the layouts this test claims to read -- a
    brace with a space inside it, a row split before its flags, and the
    function-pointer cast."""
    text = "\n".join(_c_files({**roots, "kinds": kinds}))
    assert '{ "' in text, "no row has GNU's `{ \"`"
    assert re.search(r'\{ "\w+", [^\n]*,\n\s*METH_', text), "no split row"
    assert re.search(r"\(void \(\*\) ?\(void\)\)", text), "no cast row"


def test_every_row_of_every_formatted_table_is_read(roots, kinds):
    texts = _c_files({**roots, "kinds": kinds})
    assert sum(len(_table_bodies(t)) for t in texts) > len(roots)
    unread = [u for t in texts for u in unread_rows(t)]
    assert not unread, "\n".join(unread)


def test_every_stub_agrees_with_its_formatted_row(roots):
    wrong = []
    for row, root in roots.items():
        stubs = _stubs(root)
        for cls, rows in _tables(root).items():
            for name, flags in rows.items():
                fn = stubs[cls].get(name)
                named = [a.arg for a in fn.args.posonlyargs + fn.args.args]
                named = named[1:] if cls else named
                if named and bool(fn.args.posonlyargs) == (
                    "METH_KEYWORDS" in flags
                ):
                    wrong.append(f"{row} {cls}.{name}: {flags}")
    assert not wrong, "\n".join(wrong)
