"""gh-1141: the generated copies of `[project] version` are reported.

Six generated artefacts carry the project's version. Exactly one — a
`--target pep723` app script — is regenerated glue and picks a bump up on the
next `apply`. The other five are create-only, and a bump reached none of them
with `jm status --check` reporting clean throughout.

The last of those five is the one with teeth. `<pkg>_version()` in
`native/src/<pkg>_lib.c` is a **C API**: a consumer links the library and asks
it what version it is, and was told the version the project had on the day it
was scaffolded, forever.

`apply` reports and does not rewrite, which is gh-442's answer to the
identical question and is followed rather than re-derived: a release bumps
`pyproject.toml` and never the manifest, so the manifest is often the stale
side, and rewriting an author-owned file from it on the next unrelated `apply`
would be worse than the drift. `jm config version` does rewrite (gh-2069): it
is the author declaring the value, so jm knows which side moved.

The coverage test here is deliberately **derived from the tree** rather than
from a list of files: it bumps the manifest, asks the tree which files still
carry the old string, and demands the reporter name exactly those. A generated
file that gains a version copy later is covered on the day it gains it.

GATE: every generated copy of the project version agrees with the manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import JmRun, run_cli

SRC = Path(__file__).parent.parent / "src"
TEMPLATES = SRC / "just_makeit" / "templates"

OLD = "0.1.0"
NEW = "9.9.9"


def _cli(*args, cwd) -> JmRun:
    # gh-1374: in THIS process -- the child bought isolation only.
    return run_cli(*args, cwd=cwd)


def _bump(root: Path, to: str = NEW) -> None:
    p = root / "just-makeit.toml"
    body = p.read_text(encoding="utf-8")
    assert f'version = "{OLD}"' in body
    p.write_text(body.replace(f'version = "{OLD}"', f'version = "{to}"', 1))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project carrying every artefact that holds a version copy, including
    the PEP 723 app — the one jm *does* maintain, so the tests below can show
    the difference rather than assert it."""
    assert _cli("new", "vp", cwd=tmp_path).returncode == 0
    root = tmp_path / "vp"
    assert _cli("object", "eng", cwd=root).returncode == 0
    assert (
        _cli(
            "app",
            "--target",
            "pep723",
            "--object",
            "eng",
            "--name",
            "tool",
            cwd=root,
        ).returncode
        == 0
    )
    assert _cli("status", "--check", cwd=root).returncode == 0
    return root


def _carrying(root: Path, needle: str) -> "set[str]":
    """Files under *root* whose text contains *needle*.

    The manifest is excluded (it is the input, not a copy) and so is `build/`.
    No extension filter — that is how the original survey undercounted: it
    listed `*.c`/`*.toml`/… and `Doxyfile` has no extension, so a whole copy
    was invisible to the very measurement meant to find them all.
    """
    out = set()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "build" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        if rel == "just-makeit.toml":
            continue
        try:
            if needle in p.read_text(encoding="utf-8"):
                out.add(rel)
        except (OSError, UnicodeDecodeError):
            continue
    return out


class TestReporter:
    def test_a_fresh_scaffold_is_clean(self, project: Path) -> None:
        """Nothing to report before anything is bumped — a reporter that
        fires on a untouched scaffold is one every project switches off."""
        from just_makeit import _config, _projversion

        assert _projversion.drift(project, _config.load(project)) == []

    def test_it_names_exactly_the_files_the_tree_says_are_stale(
        self, project: Path
    ) -> None:
        """The coverage claim, derived rather than listed.

        Whatever still carries the old string after a bump and an `apply` is
        what the reporter must name. A newly generated file holding a version
        lands in the left set automatically, so this fails the day it appears
        rather than the day someone remembers to look.
        """
        from just_makeit import _config, _projversion

        _bump(project)
        assert _cli("apply", cwd=project).returncode == 0
        stale = _carrying(project, OLD)
        reported = {
            v.rel for v in _projversion.drift(project, _config.load(project))
        }
        assert reported == stale

    def test_the_pep723_app_self_heals_and_is_not_reported(
        self, project: Path
    ) -> None:
        """`apply` rewrites it, so it cannot be stale by the time anything
        reads it. Reporting a file that fixes itself on the next command is
        how a gate teaches people to ignore it."""
        _bump(project)
        assert _cli("apply", cwd=project).returncode == 0
        assert f"vp=={NEW}" in (project / "tool.py").read_text(
            encoding="utf-8"
        )

    def test_the_c_api_copy_is_covered(self, project: Path) -> None:
        """Named on its own because it is the reason this is a bug and not a
        tidiness complaint: a linking consumer is told this value."""
        from just_makeit import _config, _projversion

        _bump(project)
        reported = {
            v.rel for v in _projversion.drift(project, _config.load(project))
        }
        assert "native/src/vp_lib.c" in reported


class TestStatus:
    def test_check_fails_on_version_drift_ALONE(self, project: Path) -> None:
        """`apply` first, deliberately.

        Without it this passes for the wrong reason and proves nothing: a
        bump leaves the PEP 723 script stale too, and that alone fails
        `--check`. Sabotaging the VERSION arm out of `drift_count` still left
        this green until the `apply` was added — the exit code was right and
        was being produced by something else entirely.
        """
        _bump(project)
        assert _cli("apply", cwd=project).returncode == 0
        assert _cli("status", "--check", cwd=project).returncode == 1

    def test_it_names_the_section_and_the_summary(self, project: Path) -> None:
        _bump(project)
        out = _cli("status", cwd=project)
        assert "VERSION (5)" in out.stdout, out.stdout
        assert "version-drift (!)" in out.stdout, out.stdout

    def test_status_allow_suppresses_per_file(self, project: Path) -> None:
        """A project that maintains one of these by hand can quiet exactly
        that file without quieting the C API copy beside it."""
        _bump(project)
        p = project / "just-makeit.toml"
        p.write_text(
            p.read_text(encoding="utf-8").replace(
                "[project]",
                '[project]\nstatus_allow = ["Doxyfile", "bootstrap.toml"]',
                1,
            ),
            encoding="utf-8",
        )
        out = _cli("status", cwd=project)
        assert "VERSION (3)" in out.stdout, out.stdout
        assert "Doxyfile" not in out.stdout.split("VERSION (3)")[1][:400]


class TestEveryTemplateIsClassified:
    """The registration-free half: a template that gains a version slot must
    be classified, not silently uncovered.

    `<<version>>` in a template is jm choosing to stamp the version into a
    file. Each such template is either *checked* by `_projversion` or
    *exempt* because `apply` rewrites it. A new one is neither until someone
    says which, and that is a decision worth making in a diff.
    """

    #: Regenerated on every `apply`, so they cannot hold a stale version.
    EXEMPT = {
        "py/app_pep723.py",
        "py/app_pep723_cmd.py",
        "py/app_pep723_fn.py",
    }

    #: Create-only, and therefore `_projversion`'s to report.
    CHECKED = {
        "c/src/lib_stub.c",
        "cmake/CMakeLists_top.cmake",
        "doc/Doxyfile",
        "toml/bootstrap.toml",
        "toml/pyproject.toml",
    }

    def test_no_template_stamps_a_version_unclassified(self) -> None:
        stamping = {
            p.relative_to(TEMPLATES).as_posix()
            for p in TEMPLATES.rglob("*")
            if p.is_file() and "<<version>>" in p.read_text(encoding="utf-8")
        }
        assert stamping == self.EXEMPT | self.CHECKED


class TestConfigVersionWritesEveryCopy:
    """gh-2069: `jm config version` wrote the manifest and none of the
    copies, so `status --check` failed on the next command.

    The verb is the author declaring the value, so every copy follows it:
    the five `_projversion` owns, through the same table `drift` reads, and
    the PEP 723 app, through the same replay `apply` makes. The gh-2057 gate
    holds the verb to `apply`'s tree on every project shape; these hold the
    parts it cannot see -- a copy neither oracle reports, and a write that
    must NOT happen.
    """

    def test_no_copy_of_the_old_version_is_left(self, project: Path) -> None:
        """Derived from the tree, like the reporter's coverage test above:
        whatever still carries the old string after the verb is a copy it
        missed, including the PEP 723 app and any file that gains a copy
        later."""
        r = _cli("config", "version", NEW, cwd=project)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _carrying(project, OLD) == set()
        assert _cli("status", "--check", cwd=project).returncode == 0

    def test_a_deferred_version_moves_pyproject(self, project: Path) -> None:
        """gh-1283: a manifest that omits the version reads it from
        `pyproject.toml`, and `save` keeps the omission. The verb was a
        silent no-op there -- it printed the new value and changed nothing.
        Writing the copies is what makes it take effect, and the manifest
        stays silent."""
        manifest = project / "just-makeit.toml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                f'version = "{OLD}"\n', "", 1
            ),
            encoding="utf-8",
        )
        assert _cli("config", "version", NEW, cwd=project).returncode == 0
        assert "\nversion = " not in manifest.read_text(encoding="utf-8")
        assert _carrying(project, OLD) == set()
        assert _cli("status", "--check", cwd=project).returncode == 0

    def test_a_derived_copy_stays_derived(self, tmp_path: Path) -> None:
        """gh-1204: a copy the build derives carries no value to overwrite.
        Writing a literal over it would undo the one fix that makes drift
        impossible."""
        from just_makeit import _projversion as V

        derived = "PROJECT_NUMBER = $(PKG_VERSION)\n"
        (tmp_path / "Doxyfile").write_text(derived, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "p"\nversion = "{OLD}"\n', encoding="utf-8"
        )
        cfg = {"project": {"name": "p", "version": NEW}}
        assert V.sync(tmp_path, cfg) == (["pyproject.toml"], [])
        assert (tmp_path / "Doxyfile").read_text(encoding="utf-8") == derived

    def test_only_the_project_table_is_written(self, tmp_path: Path) -> None:
        """A ``version`` under another table, ahead of ``[project]`` and
        holding the same string, is not the project's: the parser decides
        which line is, and the other is left byte-identical."""
        from just_makeit import _projversion as V

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            f'[tool.x]\nversion = "{OLD}"\n\n'
            f'[project]\nname = "p"\nversion = "{OLD}"\n',
            encoding="utf-8",
        )
        cfg = {"project": {"name": "p", "version": NEW}}
        assert V.sync(tmp_path, cfg) == (["pyproject.toml"], [])
        assert pyproject.read_text(encoding="utf-8") == (
            f'[tool.x]\nversion = "{OLD}"\n\n'
            f'[project]\nname = "p"\nversion = "{NEW}"\n'
        )

    def test_a_value_its_slot_cannot_hold_is_not_written(
        self, tmp_path: Path
    ) -> None:
        """CMake's ``project(VERSION)`` takes integers only and rejects a
        PEP 440 pre-release at configure time. Writing one would break the
        build to clear a status line, so the copy is left and named (its
        spelling is gh-2084); the copies that can hold it are written."""
        from just_makeit import _projversion as V

        cmake = f"project(p\n  VERSION {OLD}\n  LANGUAGES C)\n"
        (tmp_path / "CMakeLists.txt").write_text(cmake, encoding="utf-8")
        (tmp_path / "Doxyfile").write_text(
            f"PROJECT_NUMBER = {OLD}\n", encoding="utf-8"
        )
        cfg = {"project": {"name": "p", "version": "1.1.2a47"}}
        written, unwritable = V.sync(tmp_path, cfg)
        assert written == ["Doxyfile"]
        assert len(unwritable) == 1 and "CMakeLists.txt" in unwritable[0]
        assert (tmp_path / "CMakeLists.txt").read_text(
            encoding="utf-8"
        ) == cmake

    def test_the_project_command_is_the_one_written(
        self, tmp_path: Path
    ) -> None:
        """The writer replaces what the reader located, so the reader is
        anchored on ``project()`` -- on one line, as a formatter may leave
        it -- and never on another command's ``VERSION`` keyword, which a
        line-anchored match would have rewritten instead."""
        from just_makeit import _projversion as V

        other = "write_basic_package_version_file(\n  f\n  VERSION 3.2.1)\n"
        cmake = tmp_path / "CMakeLists.txt"
        cmake.write_text(
            f"project(p VERSION {OLD} LANGUAGES C)\n{other}", encoding="utf-8"
        )
        cfg = {"project": {"name": "p", "version": NEW}}
        assert V.sync(tmp_path, cfg) == (["CMakeLists.txt"], [])
        assert cmake.read_text(encoding="utf-8") == (
            f"project(p VERSION {NEW} LANGUAGES C)\n{other}"
        )

    def test_two_copies_in_one_file_are_neither_read_nor_written(
        self, tmp_path: Path
    ) -> None:
        """Exactly one match, or the file is not carrying a copy jm
        understands: doxygen takes the LAST of two ``PROJECT_NUMBER`` lines,
        so writing the first would change nothing doxygen reads."""
        from just_makeit import _projversion as V

        body = f"PROJECT_NUMBER = {OLD}\nPROJECT_NUMBER = 0.0.9\n"
        (tmp_path / "Doxyfile").write_text(body, encoding="utf-8")
        cfg = {"project": {"name": "p", "version": NEW}}
        assert V.drift(tmp_path, cfg) == []
        assert V.sync(tmp_path, cfg) == ([], [])
        assert (tmp_path / "Doxyfile").read_text(encoding="utf-8") == body
