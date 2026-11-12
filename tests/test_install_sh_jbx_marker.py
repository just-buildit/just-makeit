"""``jbx just-makeit:install-deps`` leaves ``jm`` on the caller's PATH.

``aliases.toml`` maps ``install-deps`` to install.sh, so ``jbx`` runs it --
but sourced into jbx's own subshell, where activating the venv reaches
nobody: the install succeeded and ``jm`` was still not found. just-bashit's
``jbx`` shell function hands a script ``JB_RELOAD_MARKER``, a file it reads
once the script returns, putting each ``path DIR`` line first on the
caller's PATH (just-bashit ``src/just_bashit/jbx-shell.sh``). install.sh
now writes its venv's ``bin`` there, instead of activating, when it is set.

The machine is `test_gh1972_installers_patchelf.Box`, as in
`test_gh2582_install_sh_sourced`: offline, nothing written outside
``tmp_path``.

GATE: sourced with JB_RELOAD_MARKER set, a successful install appends
      exactly one absolute ``path <venv>/bin`` line to it and activates
      nothing; ``--check``, a failure, or no marker write nothing to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_gh1972_installers_patchelf import BASH, Box
from test_gh2582_install_sh_sourced import (
    ACTIVATED,
    _assert_unchanged,
    _parse,
    _source,
)

pytestmark = pytest.mark.skipif(not BASH, reason="install.sh is a bash script")


def _box(tmp_path: Path, monkeypatch, marker: "Path | None") -> Box:
    """A Box whose venv's ``activate`` is observable, with *marker* set."""
    box = Box(tmp_path, "Linux")
    (box.root / "venv" / "bin" / "activate").write_text(
        f"{ACTIVATED}=1\n", encoding="utf-8"
    )
    env = box.env()
    if marker is not None:
        env["JB_RELOAD_MARKER"] = str(marker)
    monkeypatch.setattr(box, "env", lambda: dict(env))
    return box


def test_jbx_install_writes_the_venv_bin_to_the_marker(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    marker.write_text("path /already/there\n", encoding="utf-8")
    box = _box(tmp_path, monkeypatch, marker)
    status, before, after = _parse(_source(box, str(box.root / "venv")))
    assert status == 0
    assert marker.read_text(encoding="utf-8").splitlines() == [
        "path /already/there",
        f"path {(box.root / 'venv').resolve()}/bin",
    ]
    # jbx applies the marker; activating in its subshell is dead weight.
    _assert_unchanged(before, after)


def test_a_relative_venv_is_written_absolute(tmp_path, monkeypatch):
    marker = tmp_path / "marker"
    box = _box(tmp_path, monkeypatch, marker)
    # _source runs with cwd=box.root, so ./venv is box.root/venv.
    status, _, _ = _parse(_source(box, "--", "./venv"))
    assert status == 0
    assert marker.read_text(encoding="utf-8") == (
        f"path {(box.root / 'venv').resolve()}/bin\n"
    )


@pytest.mark.parametrize(
    "args, python",
    [(("--check",), None), ((), "/nonexistent/python3")],
    ids=["check", "failure"],
)
def test_no_install_writes_no_marker(tmp_path, monkeypatch, args, python):
    marker = tmp_path / "marker"
    box = _box(tmp_path, monkeypatch, marker)
    _parse(_source(box, *args, str(box.root / "venv"), python=python))
    assert not marker.exists()


def test_without_jbx_sourcing_still_activates(tmp_path, monkeypatch):
    box = _box(tmp_path, monkeypatch, None)
    res = _source(box, str(box.root / "venv"))
    status, _, after = _parse(res)
    assert status == 0
    assert "Venv activated" in res.stdout
    assert f"var {ACTIVATED}" in after
