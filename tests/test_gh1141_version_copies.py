"""gh-1141: the generated copies of `[project] version` are reported.

Six generated artefacts carry the project's version. Exactly one — a
`--target pep723` app script — is regenerated glue and picks a bump up on the
next `apply`. The other five are create-only, and a bump reached none of them
with `jm status --check` reporting clean throughout.

The last of those five is the one with teeth. `<pkg>_version()` in
`native/src/<pkg>_lib.c` is a **C API**: a consumer links the library and asks
it what version it is, and was told the version the project had on the day it
was scaffolded, forever.

jm reports and does not rewrite, which is gh-442's answer to the identical
question and is followed rather than re-derived: a release bumps
`pyproject.toml` and never the manifest, so the manifest is often the stale
side, and rewriting an author-owned file from it on the next unrelated `apply`
would be worse than the drift.

The coverage test here is deliberately **derived from the tree** rather than
from a list of files: it bumps the manifest, asks the tree which files still
carry the old string, and demands the reporter name exactly those. A generated
file that gains a version copy later is covered on the day it gains it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
TEMPLATES = SRC / "just_makeit" / "templates"

OLD = "0.1.0"
NEW = "9.9.9"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"},
    )


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
