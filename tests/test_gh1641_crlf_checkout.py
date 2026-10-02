"""gh-1641: a Windows checkout of a jm project is not drift.

jm writes LF on every platform (gh-1368), and `status` compared the tree
against that render byte for byte. Git for Windows checks text out CRLF
(``core.autocrlf=true``) unless the project says otherwise, and jm scaffolded
no ``.gitattributes``, so a fresh Windows clone read every regenerated file as
STALE and every create-only one as OUTDATED: ``status --check`` failed a
downstream's Windows CI over line endings no compiler, CMake, Python or
formatter reads differently.

Two halves, and both are gated here:

- the cause: `jm new` writes a ``.gitattributes`` that keeps a checkout LF.
    Proved with git itself, not by reading the file -- a clone made with
    ``core.autocrlf=true`` is CRLF without the attributes file and LF with it;
- the report: a file that differs from jm's render only in CRLF versus LF is
    its own uncounted LINE ENDINGS row, never STALE or OUTDATED, while a real
    change under a CRLF checkout is still STALE and diffs as that change alone.
    `apply` says ``eol`` for the rewrite to LF, not ``update``.

The CRLF tree is made in Python, so this runs on any host.

GATE: a CRLF copy of a fresh scaffold passes `status --check`; a real edit
      under it fails it; a fresh scaffold carries attributes that make an
      autocrlf clone LF.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _jmrun import run_cli
from just_makeit import _apply, _textio

_EXT = Path("native") / "src" / "g" / "g_ext.c"


def _scaffold(tmp_path: Path) -> Path:
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return tmp_path / "p"


def _to_crlf(root: Path) -> list[str]:
    """Every text file under *root* rewritten CRLF, as autocrlf checks out."""
    done = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.relative_to(root).parts:
            continue
        data = p.read_bytes()
        if b"\0" in data:
            continue
        p.write_bytes(_textio.lf(data).replace(b"\n", b"\r\n"))
        done.append(p.relative_to(root).as_posix())
    return done


def _status_json(root: Path) -> dict:
    r = run_cli("status", "--json", cwd=root)
    return json.loads(r.stdout)


@pytest.fixture
def crlf_project(tmp_path: Path) -> Path:
    root = _scaffold(tmp_path)
    clean = run_cli("status", "--check", cwd=root)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert _EXT.as_posix() in _to_crlf(root)
    assert b"\r\n" in (root / _EXT).read_bytes()
    return root


def test_a_crlf_checkout_reads_as_line_endings_not_drift(crlf_project):
    check = run_cli("status", "--check", cwd=crlf_project)
    assert check.returncode == 0, check.stdout + check.stderr
    assert "STALE (" not in check.stdout, check.stdout
    assert "OUTDATED (" not in check.stdout, check.stdout
    assert "LINE ENDINGS (" in check.stdout, check.stdout

    data = _status_json(crlf_project)
    assert data["drift"] == 0, data
    assert data["outdated"] == [], data["outdated"]
    # Not vacuous: the regenerated glue is in the row, so the replay really
    # compared CRLF bytes against an LF render.
    assert _EXT.as_posix() in data["line_endings"], data["line_endings"]
    assert not [e for e in data["entries"] if e["state"] == "stale"], data


def test_a_real_change_under_crlf_is_still_stale(crlf_project):
    ext = crlf_project / _EXT
    ext.write_bytes(ext.read_bytes() + b"/* edited */\r\n")

    check = run_cli("status", "--check", cwd=crlf_project)
    assert check.returncode == 1, check.stdout + check.stderr
    assert f"  ~ {_EXT.as_posix()}" in check.stdout.splitlines(), check.stdout

    data = _status_json(crlf_project)
    assert _EXT.as_posix() not in data["line_endings"]

    # The diff is the edit, not every line of the file.
    diff = run_cli("status", "--diff", cwd=crlf_project).stdout
    removed = [
        ln
        for ln in diff.splitlines()
        if ln.lstrip().startswith("-") and not ln.lstrip().startswith("---")
    ]
    assert [ln.strip() for ln in removed] == ["-/* edited */"], diff


def test_a_crlf_create_only_file_is_not_outdated_but_a_changed_one_is(
    crlf_project,
):
    outdated = [e["path"] for e in _status_json(crlf_project)["outdated"]]
    assert "Makefile" not in outdated, outdated
    mk = crlf_project / "Makefile"
    mk.write_bytes(mk.read_bytes() + b"# mine\r\n")
    outdated = [e["path"] for e in _status_json(crlf_project)["outdated"]]
    assert "Makefile" in outdated, outdated


def test_apply_reports_the_rewrite_to_lf_as_eol(crlf_project):
    r = run_cli("apply", cwd=crlf_project)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert f"  eol     {_EXT.as_posix()}" in lines, r.stdout
    assert not [ln for ln in lines if ln.startswith("  update  ")], r.stdout
    assert b"\r\n" not in (crlf_project / _EXT).read_bytes()


def test_changed_report_separates_line_endings_from_content(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    (root / "a.c").write_bytes(b"int a;\r\n")
    (root / "b.c").write_bytes(b"int b;\r\n")
    before = _apply._tree_digests(root)
    (root / "a.c").write_bytes(b"int a;\n")
    (root / "b.c").write_bytes(b"int b = 1;\n")
    got = _apply._changed_report(root, before, ["a.c", "b.c"])
    assert got == [("eol", "a.c"), ("update", "b.c")]


def _git(cwd: Path, *args: str) -> str:
    """git with no user or system configuration: a global ``eol`` or
    attributes file would make the control clone LF and prove nothing."""
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        XDG_CONFIG_HOME=str(cwd),
        HOME=str(cwd),
    )
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=" + os.devnull,
            *args,
        ],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _autocrlf_clone(tmp_path: Path, project: Path, name: str) -> Path:
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "scaffold")
    _git(
        tmp_path,
        "-c",
        "core.autocrlf=true",
        "clone",
        "-q",
        "--config",
        "core.autocrlf=true",
        str(project),
        name,
    )
    return tmp_path / name


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_scaffold_keeps_an_autocrlf_clone_lf(tmp_path):
    project = _scaffold(tmp_path)
    attrs = (project / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in attrs.splitlines(), attrs

    clone = _autocrlf_clone(tmp_path, project, "clone")
    crlf = [
        p.relative_to(clone).as_posix()
        for p in clone.rglob("*")
        if p.is_file()
        and ".git" not in p.relative_to(clone).parts
        and b"\r\n" in p.read_bytes()
    ]
    assert crlf == [], crlf
    check = run_cli("status", "--check", cwd=clone)
    assert check.returncode == 0, check.stdout + check.stderr
    assert "LINE ENDINGS" not in check.stdout, check.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_without_the_attributes_an_autocrlf_clone_is_crlf(tmp_path):
    """The control: the clone above is LF BECAUSE of the file, not because
    this git or this host would have checked it out LF anyway."""
    project = _scaffold(tmp_path)
    (project / ".gitattributes").unlink()
    clone = _autocrlf_clone(tmp_path, project, "clone")
    assert b"\r\n" in (clone / _EXT).read_bytes()
