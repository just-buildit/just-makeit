"""gh-1576: a ``find_packages`` entry names its pkg-config face, or says so.

The table form, ``{ name, pkg_config | libs_private }``, is the one place a
dependency is declared: ``name`` feeds ``find_package``/``find_dependency``,
``pkg_config`` the installed ``.pc``'s ``Requires.private`` and
``libs_private`` its ``Libs.private`` (pc(5)'s two cases). These pin what
the installed-consumer gate (test_gh1576_dep_usage_both_faces.py) cannot see
from outside: a malformed entry is refused rather than dropped, the manifest
round-trips the table, and ``status`` names a bare entry the ``.pc`` cannot.
"""

from __future__ import annotations

import pytest

from _jmrun import run_cli
from just_makeit import _config as C


def _cfg(*entries):
    return {"project": {"find_packages": list(entries)}}


def test_string_and_table_entries_read_alike():
    got = C.find_package_entries(
        _cfg(
            "Fftw3",
            {"name": "Doppler", "pkg_config": "doppler"},
            {"name": "Threads", "libs_private": "-pthread"},
        )
    )
    assert got == [
        C.FindPackage("Fftw3"),
        C.FindPackage("Doppler", pkg_config="doppler"),
        C.FindPackage("Threads", libs_private="-pthread"),
    ]
    assert C.find_packages(_cfg("A", {"name": "B"})) == ["A", "B"]


@pytest.mark.parametrize(
    "entry",
    [
        {"pkg_config": "x"},  # no name
        {"name": "X", "pkgconfig": "x"},  # misspelt key
        {"name": "X", "pkg_config": ""},  # empty value
        {"name": "X", "pkg_config": 3},  # not a string
        7,  # neither a name nor a table
    ],
)
def test_a_malformed_entry_is_refused(entry, capsys):
    with pytest.raises(SystemExit):
        C.find_package_entries(_cfg(entry))
    assert "find_packages entry" in capsys.readouterr().err


@pytest.fixture
def project(tmp_path):
    assert run_cli("new", "p", "--object", "a", cwd=tmp_path).returncode == 0
    return tmp_path / "p"


def test_the_manifest_round_trips_a_table_entry(project):
    cfg = C.load(project)
    decl = ["Fftw3", {"name": "Doppler", "pkg_config": "doppler"}]
    cfg["project"]["find_packages"] = decl
    C.save(project, cfg)
    assert C.load(project)["project"]["find_packages"] == decl


def test_a_fresh_manifest_writes_a_table_entry(tmp_path):
    """A brand-new file (and apply's scratch manifest) is written by
    `_dump`, not tomlkit -- the path that used to quote every list item,
    turning a table into the string of its Python repr (gh-763's shape)."""
    decl = ["Fftw3", {"name": "Doppler", "pkg_config": "doppler"}]
    C.save(tmp_path, {"project": {"name": "p", "find_packages": decl}})
    assert C.load(tmp_path)["project"]["find_packages"] == decl


def test_both_pc_fields_reach_the_root(project):
    cfg = C.load(project)
    cfg["project"]["find_packages"] = [
        {"name": "Doppler", "pkg_config": "doppler"},
        {"name": "Threads", "libs_private": "-pthread"},
    ]
    cfg["project"]["pkg_modules"] = ["zlib"]
    C.save(project, cfg)
    assert run_cli("apply", cwd=project).returncode == 0
    root = (project / "CMakeLists.txt").read_text()
    assert "find_package(Doppler REQUIRED)" in root
    assert "find_dependency(Threads)" in root
    assert 'set(JM_PC_REQUIRES_PRIVATE "Requires.private: doppler, zlib")' in (
        root
    )
    assert 'set(JM_PC_LIBS_PRIVATE "Libs.private: -pthread")' in root
    pc_in = next((project / "cmake").glob("*.pc.in")).read_text()
    assert "@JM_PC_REQUIRES_PRIVATE@" in pc_in
    assert "@JM_PC_LIBS_PRIVATE@" in pc_in


def test_status_names_a_dependency_the_pc_cannot(project):
    cfg = C.load(project)
    cfg["project"]["find_packages"] = [
        "Fftw3",
        {"name": "Doppler", "pkg_config": "doppler"},
    ]
    C.save(project, cfg)
    assert run_cli("apply", cwd=project).returncode == 0
    out = run_cli("status", cwd=project).stdout
    assert "PKG-CONFIG (1)" in out, out
    block = out[out.index("PKG-CONFIG (1)") :]
    assert "~ Fftw3" in block.splitlines()[1], block
    assert "Doppler" not in block.split("\n\n")[0], block


def test_status_is_quiet_when_every_dependency_is_named(project):
    cfg = C.load(project)
    cfg["project"]["find_packages"] = [
        {"name": "Doppler", "pkg_config": "doppler"}
    ]
    C.save(project, cfg)
    assert run_cli("apply", cwd=project).returncode == 0
    assert "PKG-CONFIG" not in run_cli("status", cwd=project).stdout
