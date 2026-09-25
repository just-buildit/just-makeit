"""gh-1581: one library, one name -- on every face a consumer uses.

The pkg-config guide: the ``.pc`` file name IS the package name, and a
library ``libfoo`` should ship ``foo.pc``. jm named it after the project with
``_`` spelled ``-``, so ``libmy_proj.so`` came with ``my-proj.pc`` beside
``find_package(my_proj)``: three spellings of one library. And the exported
targets were ``my_proj::my_proj_lib`` / ``..._lib_static`` where
cmake-packages(7) shows ``<Pkg>::<Pkg>``.

Now the ``.pc``, its template and ``Name:`` use the library's name, and the
exported targets are ``<pkg>::<pkg>`` and ``<pkg>::<pkg>-static``
(``EXPORT_NAME``; inside the build they stay ``<pkg>_lib*``). An older
project's ``cmake/<pkg-with-hyphens>.pc.in`` is a `_createonly.RENAMED` pair:
`apply` will not create the new name beside it, `status` names it, and
`upgrade` renames it -- carrying its ownership token, or the moved file would
silently become the author's. The consumer matrix and smoke build against
the new names.

GATE: the .pc, its template and the exported targets carry the library's own
      name, and an older project's hyphenated .pc template is renamed by
      `upgrade` with its ownership intact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _createonly
from just_makeit import _render as R


@pytest.fixture
def proj(tmp_path) -> Path:
    r = run_cli("new", "my_proj", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / "my_proj"


def test_every_face_carries_the_librarys_name(proj):
    assert (proj / "cmake" / "my_proj.pc.in").is_file()
    assert not (proj / "cmake" / "my-proj.pc.in").exists()
    pc_in = (proj / "cmake" / "my_proj.pc.in").read_text(encoding="utf-8")
    assert "\nName: my_proj\n" in pc_in, pc_in
    root = (proj / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "EXPORT_NAME my_proj)" in root
    assert "EXPORT_NAME my_proj-static)" in root
    assert "my-proj" not in root, "a hyphenated name is left in the root file"


def _as_older_jm_left_it(proj: Path) -> Path:
    """The template under its old, hyphenated name, owned by that name."""
    new = proj / "cmake" / "my_proj.pc.in"
    old = proj / "cmake" / "my-proj.pc.in"
    text = new.read_text(encoding="utf-8")
    text = text.replace(R.owned_token(new.name), R.owned_token(old.name))
    old.write_text(text, encoding="utf-8")
    new.unlink()
    return old


def test_apply_holds_the_new_name_back_and_upgrade_renames(proj):
    old = _as_older_jm_left_it(proj)
    new = proj / "cmake" / "my_proj.pc.in"
    assert _createonly.superseded(proj) == [
        ("cmake/my-proj.pc.in", "cmake/my_proj.pc.in")
    ]

    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not new.exists(), "apply created the new name beside the old"
    status = run_cli("status", cwd=proj)
    assert "cmake/my-proj.pc.in" in status.stdout, status.stdout

    up = run_cli("upgrade", cwd=proj)
    assert up.returncode == 0, up.stdout + up.stderr
    assert not old.exists() and new.is_file()
    text = new.read_text(encoding="utf-8")
    # Ownership came along: apply keeps rendering it.
    assert R.is_owned_render(text, new.name), text[:120]
    assert _createonly.superseded(proj) == []
    again = run_cli("apply", cwd=proj)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "PACKAGING" not in run_cli("status", cwd=proj).stdout


def test_a_name_without_an_underscore_renames_nothing(tmp_path):
    r = run_cli("new", "plain", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    proj = tmp_path / "plain"
    assert (proj / "cmake" / "plain.pc.in").is_file()
    assert _createonly.superseded(proj) == []
