"""gh-1591 phase 3: `jm upgrade` respells an existing tree onto its c_prefix.

Adding `[project] c_prefix` to a project renames every C symbol jm derives,
and the C the author wrote -- the sacred `_core.h` / `_core.c`, a module
function's source, tests, benchmarks, `native/examples/` -- still calls the
old names. Phase 2a refuses that tree; `jm upgrade` moves it.

The rename set is phase 2a's (`_csym.renames` of a replay: exactly what jm
renders prefixed for THIS manifest) and so is the matcher
(`_csym.old_names_pattern`: whole identifier, case-sensitive), rewritten
through gh-1248's code-only respell, over gh-1583's file walk.

The fixture is the gh-1633 broad tree (standalone, perf, every method and
property shape, a module with a view and a function, a composer with a
bridge, a handle, process_global) built UNPREFIXED, with author C added
where each wrong respell would show:

- an author macro that is a derived name up to case (`FIR_STEP`),
- an author identifier that extends a derived one (`fir_create_default`),
- a comment and a string quoting `fir_create`,
- a program under `native/examples/`, and a nested project beside it.

GATE: after `c_prefix` + `jm upgrade`, `apply` passes, `status --check` is
      clean and no derived symbol escapes the prefix; the author's macros,
      identifiers, comments, strings and nested projects are untouched while
      `native/examples/` is respelled; a second upgrade changes nothing; the
      printed rename table is a TSV; and a changed or removed prefix is
      refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _csym_fixtures as FX
from _jmrun import run_cli
from just_makeit import _config as C

PREFIX = FX.MARK.rstrip("_")

_AUTHOR_H = "#define FIR_STEP 4 /* an author macro: fir_step, up to case */\n"
_AUTHOR_C = """
/* fir_create() is quoted in this comment and must stay */
static const char *fir_note = "fir_create";
double fir_create_default(void);
double fir_create_default(void)
{
    fir_state_t *s = fir_create(1.0, 4);
    double g = fir_get_level(s) + FIR_STEP * 0;
    fir_destroy(s);
    (void)fir_note;
    return g;
}
"""
_EXAMPLE = """#include "std/fir/fir_core.h"
int main(void)
{
    fir_state_t *s = fir_create(1.0, 4);
    fir_destroy(s);
    return 0;
}
"""


def _set_prefix(root: Path) -> None:
    toml = root / C.FILENAME
    text = toml.read_text(encoding="utf-8")
    toml.write_text(
        text.replace("[project]\n", f'[project]\nc_prefix = "{PREFIX}"\n', 1),
        encoding="utf-8",
    )


def _author(root: Path) -> None:
    h = next((root / "native" / "inc").rglob("fir_core.h"))
    text = h.read_text(encoding="utf-8")
    h.write_text(
        text.replace("typedef struct", _AUTHOR_H + "typedef struct", 1),
        encoding="utf-8",
    )
    c = root / "native" / "src" / "fir" / "fir_core.c"
    c.write_text(c.read_text(encoding="utf-8") + _AUTHOR_C, encoding="utf-8")
    ex = root / "native" / "examples"
    ex.mkdir(parents=True)
    (ex / "demo.c").write_text(_EXAMPLE, encoding="utf-8")
    # A nested project -- its own manifest -- is its own upgrade's business.
    nested = root / "native" / "examples" / "downstream"
    nested.mkdir()
    (nested / C.FILENAME).write_text('[project]\nname = "ds"\n', "utf-8")
    (nested / "use.c").write_text("void f(void) { fir_create(1.0, 4); }\n")


@pytest.fixture(scope="module")
def upgraded(tmp_path_factory):
    roots = FX.build(tmp_path_factory.mktemp("p3"))
    _author(roots["std"])
    # Recorded, not asserted: a wrong respell that makes a command fail must
    # fail a named test below, not error the fixture.
    first, second, runs = {}, {}, {}
    for row, root in roots.items():
        _set_prefix(root)
        steps = []
        for key, cmd in (
            ("first", "upgrade"),
            ("second", "upgrade"),
            ("apply", "apply"),
        ):
            r = run_cli(cmd, cwd=root)
            steps.append((key, r.returncode, r.stdout + r.stderr))
            if key == "first":
                first[row] = r.stdout
            elif key == "second":
                second[row] = r.stdout
        runs[row] = steps
    return roots, first, second, runs


def test_the_upgraded_tree_is_clean(upgraded):
    roots, _first, _second, runs = upgraded
    for row, root in roots.items():
        for key, rc, out in runs[row]:
            assert rc == 0, (row, key, out)
        r = run_cli("status", "--check", cwd=root)
        assert r.returncode == 0, (row, r.stdout + r.stderr)


def test_no_derived_symbol_escapes_the_prefix(upgraded):
    roots, _first, _second, _runs = upgraded
    bad = [
        f"{row}/{ln}"
        for row, root in roots.items()
        for ln in FX.escapes(root)
        # The nested project is another project; kept bare on purpose.
        if not ln.startswith("native/examples/downstream/")
    ]
    assert bad == [], "left unprefixed after jm upgrade:\n" + "\n".join(bad)


def test_the_authors_own_c_is_left_as_written(upgraded):
    roots, _first, _second, _runs = upgraded
    root = roots["std"]
    h = next((root / "native" / "inc").rglob("fir_core.h")).read_text()
    assert _AUTHOR_H in h, h
    c = (root / "native" / "src" / "fir" / "fir_core.c").read_text()
    for kept in (
        "/* fir_create() is quoted in this comment and must stay */",
        'static const char *fir_note = "fir_create";',
        "double fir_create_default(void)",
        "FIR_STEP * 0",
    ):
        assert kept in c, (kept, c)
    # ...while the derived API it calls moved.
    assert f"{PREFIX}_fir_state_t *s = {PREFIX}_fir_create(1.0, 4);" in c, c
    assert f"{PREFIX}_fir_get_level(s)" in c, c


def test_native_examples_are_respelled(upgraded):
    roots, _first, _second, _runs = upgraded
    demo = (roots["std"] / "native" / "examples" / "demo.c").read_text()
    assert f"{PREFIX}_fir_state_t *s = {PREFIX}_fir_create(" in demo, demo
    assert f"{PREFIX}_fir_destroy(s)" in demo, demo


def test_a_nested_project_is_not_touched(upgraded):
    roots, _first, _second, _runs = upgraded
    use = roots["std"] / "native" / "examples" / "downstream" / "use.c"
    assert use.read_text() == "void f(void) { fir_create(1.0, 4); }\n"


def test_the_upgrade_says_what_it_changed(upgraded):
    roots, first, _second, _runs = upgraded
    out = first["std"]
    assert "native/examples/demo.c" in out, out
    assert "native/src/fir/fir_core.c" in out, out
    table = [ln.split("\t") for ln in out.splitlines() if ln.count("\t") == 1]
    assert ["fir_create", f"{PREFIX}_fir_create"] in table, out
    assert ["FIR_CORE_H", f"{PREFIX.upper()}_FIR_CORE_H"] in table, out
    # Every row is a pure old -> new pair: nothing else carries a tab.
    assert all(
        new == f"{PREFIX}_{old}" or new == f"{PREFIX.upper()}_{old}"
        for old, new in table
    ), table


def test_a_second_upgrade_changes_nothing(upgraded):
    _roots, _first, second, _runs = upgraded
    for row, out in second.items():
        assert "respelled" not in out, (row, out)
        assert "\t" not in out, (row, out)


# ── a prefix that is changed or removed is refused (gh-1650) ────────────────


def _prefixed(tmp_path: Path, p: str) -> Path:
    r = run_cli("new", "q", "--c-prefix", p, "--object", "fir", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / "q"


@pytest.mark.parametrize("to", ["bb", None], ids=["changed", "removed"])
def test_a_changed_or_removed_prefix_is_refused(tmp_path, to):
    root = _prefixed(tmp_path, "aa")
    toml = root / C.FILENAME
    text = toml.read_text(encoding="utf-8")
    new = 'c_prefix = "bb"\n' if to else ""
    toml.write_text(text.replace('c_prefix = "aa"\n', new), encoding="utf-8")
    before = FX.tree(root)
    for cmd in ("apply", "upgrade"):
        r = run_cli(cmd, cwd=root)
        assert r.returncode == 1, (cmd, r.stdout)
        assert "already carry the prefix `aa`" in r.stderr, (cmd, r.stderr)
        assert "gh-1650" in r.stderr
    assert FX.tree(root) == before, "a refused command wrote files"
