"""gh-1578: a ``pkg_modules`` entry may carry a pc(5) version bound.

``pkg_modules = ["zlib >= 1.2"]`` is how pc(5) and ``pkg_check_modules``
both spell a bounded dependency, and jm pasted the whole string into every
face: the variable prefix (``ZLIB >= 1.2``), the ``PkgConfig::`` target, and
the module list, where FindPkgConfig read ``>=`` as a module and configure
failed. Only the ``.pc`` came out right.

One reader, :func:`just_makeit._config.pkg_module_entries`, splits the name
from the bound; each face takes the spelling it needs. These pin what the
installed-consumer gate (test_gh1576_dep_usage_both_faces.py, style ``pv``)
cannot see from outside: every accepted spelling, a refusal for the rest,
and that the refusal comes before ``apply`` writes anything.
"""

from __future__ import annotations

import pytest

from _jmrun import run_cli
from just_makeit import _config as C


def _cfg(*entries):
    return {"project": {"pkg_modules": list(entries)}}


@pytest.mark.parametrize(
    "entry, name, op, version",
    [
        ("fftw3f", "fftw3f", "", ""),
        ("zlib >= 1.2", "zlib", ">=", "1.2"),
        ("zlib>=1.2", "zlib", ">=", "1.2"),
        ("zlib<=2", "zlib", "<=", "2"),
        ("zlib = 1.3.1", "zlib", "=", "1.3.1"),
        ("zlib < 2", "zlib", "<", "2"),
        ("zlib > 1", "zlib", ">", "1"),
        ("gtk+-3.0 >= 3.24", "gtk+-3.0", ">=", "3.24"),
    ],
)
def test_each_pc5_spelling_splits(entry, name, op, version):
    (m,) = C.pkg_module_entries(_cfg(entry))
    assert (m.name, m.op, m.version) == (name, op, version)
    assert m.prefix == name.upper()
    assert m.cmake_spec == f"{name}{op}{version}"
    assert m.pc_spec == (f"{name} {op} {version}" if op else name)
    assert C.pkg_modules(_cfg(entry)) == [name]


@pytest.mark.parametrize(
    "entry",
    ["zlib >> 1.2", "zlib >=", ">= 1.2", "zlib 1.2", "", "a b", 3, None],
)
def test_anything_else_is_refused(entry, capsys):
    with pytest.raises(SystemExit):
        C.pkg_module_entries(_cfg(entry))
    assert "pkg_modules entry" in capsys.readouterr().err


@pytest.fixture
def project(tmp_path):
    assert run_cli("new", "p", "--object", "a", cwd=tmp_path).returncode == 0
    return tmp_path / "p"


def test_each_face_gets_its_spelling(project):
    cfg = C.load(project)
    cfg["project"]["pkg_modules"] = ["zlib >= 1.2", "fftw3f"]
    C.save(project, cfg)
    assert run_cli("apply", cwd=project).returncode == 0
    root = (project / "CMakeLists.txt").read_text()
    # The root and the installed config's find_dependency block both.
    line = 'pkg_check_modules(ZLIB REQUIRED IMPORTED_TARGET "zlib>=1.2")'
    assert root.count(line) == 2, root
    assert root.count('IMPORTED_TARGET "fftw3f")') == 2, root
    assert (
        'set(JM_PC_REQUIRES_PRIVATE "Requires.private: zlib >= 1.2, fftw3f")'
        in root
    )
    assert ">= 1.2 REQUIRED" not in root


def test_new_refuses_before_scaffolding(tmp_path):
    r = run_cli("new", "q", "--pkg-module", "zlib >> 1", cwd=tmp_path)
    assert r.returncode != 0
    assert "pkg_modules entry 'zlib >> 1'" in r.stderr
    assert not (tmp_path / "q").exists()


def test_new_records_a_bounded_module(tmp_path):
    r = run_cli("new", "q", "--pkg-module", "zlib >= 1.2", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    got = C.pkg_module_entries(C.load(tmp_path / "q"))
    assert got == [C.PkgModule("zlib", ">=", "1.2")]


def test_apply_refuses_before_writing(project):
    """Before anything, including the `jm_version` stamp. A project last
    applied by an older jm is the case that shows it: a fresh scaffold is
    already stamped, so a late refusal would leave it untouched too."""
    cfg = C.load(project)
    cfg["project"]["pkg_modules"] = ["zlib >> 1.2"]
    cfg["project"]["jm_version"] = "0.1.0"
    C.save(project, cfg)
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    r = run_cli("apply", cwd=project)
    assert r.returncode != 0
    assert "pkg_modules entry 'zlib >> 1.2'" in r.stderr
    after = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert after == before
