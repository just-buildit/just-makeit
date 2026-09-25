"""gh-1599: `[project] public_link_libs` / `public_defines`, the manifest side.

What needs no build: a malformed entry is refused before anything is
written, the flags leave the root CMakeLists when they leave the manifest
(the external-deps block used to keep its last contents once nothing was
declared), and a replayed `jm script` names them rather than dropping them.
That every face is honoured is proven by building and consuming a package
in ``tests/test_gh1599_public_flags.py``.

GATE: `[project] public_link_libs` and `public_defines` are refused when
      malformed before anything is written, leave the root CMakeLists when
      removed from the manifest, and are named by `jm script`.
"""

from __future__ import annotations

import pytest

from _jmrun import run_cli
from just_makeit import _config as C


def _project(tmp_path, **project):
    assert run_cli("new", "p", "--object", "g", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    cfg = C.load(proj)
    cfg["project"].update(project)
    C.save(proj, cfg)
    return proj


def _tree(proj):
    return {
        p.relative_to(proj): p.read_bytes()
        for p in sorted(proj.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize(
    "key, value, says",
    [
        ("public_link_libs", ["Threads::Threads"], "find_packages"),
        ("public_link_libs", ["pthread"], "-lpthread"),
        ("public_link_libs", ["-l m"], "not one linker"),
        ("public_defines", ["-D_GNU_SOURCE"], "without `-D`"),
        ("public_defines", ["1BAD"], "not a C definition"),
    ],
)
def test_a_bad_entry_is_refused_before_anything_is_written(
    tmp_path, key, value, says
):
    proj = _project(tmp_path, **{key: value})
    # A file apply would restore, so a refusal that came only once apply
    # reached the root CMakeLists would come after that write.
    (proj / "src" / "p" / "g.pyi").unlink()
    before = _tree(proj)
    r = run_cli("apply", cwd=proj)
    assert r.returncode != 0, r.stdout
    assert says in r.stderr, r.stderr
    assert _tree(proj) == before


def test_the_flags_leave_the_build_when_they_leave_the_manifest(tmp_path):
    proj = _project(
        tmp_path,
        public_link_libs=["-lxtra"],
        public_defines=["_GNU_SOURCE"],
    )
    assert run_cli("apply", cwd=proj).returncode == 0
    root = proj / "CMakeLists.txt"
    assert "set(JM_PUBLIC_LINK_LIBS -lxtra)" in root.read_text()
    assert "set(JM_PUBLIC_DEFINES _GNU_SOURCE)" in root.read_text()
    cfg = C.load(proj)
    del cfg["project"]["public_link_libs"]
    del cfg["project"]["public_defines"]
    C.save(proj, cfg)
    assert run_cli("apply", cwd=proj).returncode == 0
    text = root.read_text()
    assert "-lxtra" not in text and "_GNU_SOURCE" not in text, text


def test_script_names_them_instead_of_dropping_them(tmp_path):
    proj = _project(
        tmp_path,
        public_link_libs=["-lxtra"],
        public_defines=["_GNU_SOURCE"],
    )
    out = run_cli("script", cwd=proj).stdout
    assert '[project] public_link_libs = ["-lxtra"]' in out, out
    assert '[project] public_defines = ["_GNU_SOURCE"]' in out, out
