"""gh-1448, the write half: `jm adopt <obj>` flips only what loses nothing.

`adopt --check` said what a flip would do; the flip itself was a hand edit
of the manifest plus deleting the fragment -- the walkthrough
`stale_project` teaches -- with nothing between the author and a fragment
that held their code. `adopt` makes it one step and puts the check in it.

Each shape below is planted in a real scaffold and driven through the CLI:

* a unit whose render only ADDS code (an old render missing a later
  guard) -- taken by ``--accept-additions``;
* a unit whose render would REMOVE code (a hand-written statement) --
  taken only by naming it with ``--accept``;
* a unit that exists only on disk -- never taken.

And for every refusal, the manifest and the fragment are byte-identical
afterwards: all or nothing per object.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

FRAG = Path("native/src/dsp/dsp_ext_fir.c")
OBJ_TOML = Path("objects/fir.toml")
GUARD = """    if (!self->handle) {
        PyErr_SetString(PyExc_RuntimeError, "destroyed");
        return NULL;
    }
    fir_reset(self->handle);"""


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    assert run_cli("new", "q", "--no-c-prefix", cwd=tmp_path).returncode == 0
    root = tmp_path / "q"
    assert run_cli("module", "dsp", cwd=root).returncode == 0
    r = run_cli(
        "object",
        "fir",
        "--module",
        "dsp",
        "--state",
        "scale:float:1.0",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=root,
    )
    assert r.returncode == 0, r.stderr
    return root


def _edit_reset(root: Path, new: str) -> None:
    f = root / FRAG
    text = f.read_text(encoding="utf-8")
    assert text.count(GUARD) == 1, "fixture anchor moved"
    f.write_text(text.replace(GUARD, new), encoding="utf-8")


def _snapshot(root: Path) -> tuple:
    return (root / FRAG).read_bytes(), (root / OBJ_TOML).read_bytes()


def _flipped(root: Path) -> bool:
    toml = (root / OBJ_TOML).read_text(encoding="utf-8")
    return 'fragment = "generated"' in toml


def test_a_clean_fragment_flips_and_stays_clean(proj: Path) -> None:
    r = run_cli("adopt", "fir", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "flips                fir" in r.stdout
    assert _flipped(proj)
    assert (proj / FRAG).is_file()
    assert run_cli("status", "--check", cwd=proj).returncode == 0
    again = run_cli("adopt", "--check", cwd=proj)
    assert "generated" in again.stdout


def test_an_additions_only_unit_needs_the_flag(proj: Path) -> None:
    _edit_reset(proj, "    fir_reset(self->handle);")
    check = run_cli("adopt", "--check", cwd=proj)
    assert "adds only: fn:Fir_reset" in check.stdout

    before = _snapshot(proj)
    r = run_cli("adopt", "fir", cwd=proj)
    assert r.returncode == 1
    assert "fn:Fir_reset" in r.stdout
    assert _snapshot(proj) == before

    r = run_cli("adopt", "fir", "--accept-additions", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _flipped(proj)
    assert GUARD in (proj / FRAG).read_text(encoding="utf-8")


def test_a_unit_the_render_would_cut_is_taken_only_by_name(
    proj: Path,
) -> None:
    _edit_reset(proj, GUARD + "\n    fir_reset(self->handle); /* hand */")
    check = run_cli("adopt", "--check", cwd=proj)
    assert "differs:   fn:Fir_reset" in check.stdout

    before = _snapshot(proj)
    r = run_cli("adopt", "fir", "--accept-additions", cwd=proj)
    assert r.returncode == 1
    assert _snapshot(proj) == before

    r = run_cli("adopt", "fir", "--accept", "fn:Fir_reset", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _flipped(proj)
    assert "/* hand */" not in (proj / FRAG).read_text(encoding="utf-8")


def test_a_unit_only_on_disk_is_never_taken(proj: Path) -> None:
    f = proj / FRAG
    f.write_text(
        f.read_text(encoding="utf-8")
        + "\nstatic PyObject *\nFir_mine(PyObject *self)\n"
        "{\n    Py_RETURN_NONE;\n}\n",
        encoding="utf-8",
    )
    before = _snapshot(proj)
    r = run_cli("adopt", "fir", "--accept-additions", cwd=proj)
    assert r.returncode == 1
    assert "REFUSES" in r.stdout and "fn:Fir_mine" in r.stdout
    assert _snapshot(proj) == before


def test_an_accept_that_matches_nothing_is_refused(proj: Path) -> None:
    before = _snapshot(proj)
    r = run_cli("adopt", "fir", "--accept", "fn:Fir_typo", cwd=proj)
    assert r.returncode == 2
    assert "fn:Fir_typo" in r.stderr
    assert _snapshot(proj) == before


def test_adopt_writes_nothing_without_a_named_target(proj: Path) -> None:
    before = _snapshot(proj)
    r = run_cli("adopt", cwd=proj)
    assert r.returncode == 2
    assert _snapshot(proj) == before


def test_module_and_all_reach_the_object(proj: Path) -> None:
    assert run_cli("adopt", "--module", "dsp", cwd=proj).returncode == 0
    assert _flipped(proj)
    r = run_cli("adopt", "--all", cwd=proj)
    assert r.returncode == 0
    assert "already jm's" in r.stdout
