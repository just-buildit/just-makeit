"""gh-2083: the replay renders the project's version, not the `new` default.

`apply` compares every create-only copy of `[project] version` against a
replay of the scaffold. That replay ran `_new.run` with no version, so every
file `new` renders was rendered at `0.1.0`. For a project at any other version
that made two things wrong:

1. `status` reported an in-sync `Doxyfile` as OUTDATED, because the replay's
   copy said `0.1.0` and the real one said the project's version.
2. `apply` materialised a *missing* copy at `0.1.0`, then warned in the same
   run that the file it had just written disagreed with the manifest.

GATE: at a non-default version with every copy in sync, `status` reports no
OUTDATED for a versioned file, and `apply` over a missing `Doxyfile` writes the
manifest's version.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

#: Every file a `new` project carries a version copy in, on the cmake backend.
COPIES = (
    "pyproject.toml",
    "bootstrap.toml",
    "CMakeLists.txt",
    "Doxyfile",
    "native/src/vp_lib.c",
)

OLD = "0.1.0"
NEW = "2.4.0"


def _cli(*args, cwd) -> object:
    return run_cli(*args, cwd=cwd)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project bumped to NEW the way `status`'s advice says to do it by
    hand: the manifest and every copy, in one go, so every copy agrees."""
    assert _cli("new", "vp", cwd=tmp_path).returncode == 0
    root = tmp_path / "vp"
    assert _cli("object", "eng", cwd=root).returncode == 0
    for rel in ("just-makeit.toml", *COPIES):
        p = root / rel
        text = p.read_text(encoding="utf-8")
        assert OLD in text, rel
        p.write_text(text.replace(OLD, NEW), encoding="utf-8")
    return root


class TestOutdated:
    def test_an_in_sync_project_reports_no_version_drift(
        self, project: Path
    ) -> None:
        """Before the fix this named Doxyfile and bootstrap.toml: the replay
        rendered them at 0.1.0, so a copy that matched the manifest read as
        stale."""
        from just_makeit import _config, _projversion

        assert _projversion.drift(project, _config.load(project)) == []

    def test_status_check_is_clean(self, project: Path) -> None:
        r = _cli("status", "--check", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "VERSION (" not in r.stdout


class TestMaterialise:
    def test_apply_writes_the_manifest_version_into_a_missing_copy(
        self, project: Path
    ) -> None:
        """`apply` creates a missing Doxyfile, and it must carry the manifest's
        version. The warning it used to print in the same run is the proof the
        materialised file was wrong: it must not appear now."""
        (project / "Doxyfile").unlink()
        r = _cli("apply", cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        text = (project / "Doxyfile").read_text(encoding="utf-8")
        assert f"PROJECT_NUMBER         = {NEW}" in text
        assert "says version" not in r.stdout + r.stderr
