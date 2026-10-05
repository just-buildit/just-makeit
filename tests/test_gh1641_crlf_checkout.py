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
    ``core.autocrlf=true`` is CRLF without the attributes file and LF with it,
    reads clean, and `apply` rewrites nothing in it;
- the report: a file that differs from jm's render only in CRLF versus LF is
    its own uncounted LINE ENDINGS row, never STALE or OUTDATED, while a real
    change under a CRLF checkout is still STALE and diffs as that change alone.
    `apply` says ``eol`` for the rewrite to LF, not ``update``. Asked of two
    CRLF trees: one rewritten in Python, and a real autocrlf clone of a
    project that predates the attributes file and has since been given it,
    which is what `jm apply` does to an older project (docs/windows.md).

gh-1839: everywhere but one job, the clones SIMULATE Git for Windows -- the
host's git configuration isolated and ``core.autocrlf=true`` forced -- so
this runs on any host. A model of Git for Windows is not Git for Windows,
so the `Examples (windows-latest, clang-cl)` job in ci.yml runs this file
with ``JM_CRLF_HOST_GIT=1``, and the clones are then made with the runner's
own configuration: the default a Windows user's clone gets. The control is
what keeps that honest. A runner whose default checked text out LF would
make the attributes clone LF for the wrong reason, and the control, which
requires a clone without the attributes to be CRLF, fails there.

GATE: a CRLF copy of a fresh scaffold, and a real autocrlf clone, pass
      `status --check`; a real edit under either fails it; a fresh scaffold
      carries attributes that make an autocrlf clone LF, read clean, and
      leave `apply` nothing to rewrite; the Windows CI job runs this file
      against the runner's own git configuration.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from _jmrun import run_cli
from just_makeit import _apply, _textio

_EXT = Path("native") / "src" / "g" / "g_ext.c"
ROOT = Path(__file__).resolve().parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
WINDOWS_JOB = "examples-windows"

#: gh-1839: ``1`` makes every clone here with the host's own git
#: configuration, as it stands. Set by the Windows CI job alone (see the
#: last test), where that configuration is Git for Windows' default.
HOST_GIT_ENV = "JM_CRLF_HOST_GIT"
HOST_GIT = os.environ.get(HOST_GIT_ENV) == "1"

# Never a skip where the runner's git is the subject: there, no git fails.
needs_git = pytest.mark.skipif(
    shutil.which("git") is None and not HOST_GIT, reason="needs git"
)


def _scaffold(tmp_path: Path) -> Path:
    r = run_cli("new", "p", "--object", "g", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    root = tmp_path / "p"
    clean = run_cli("status", "--check", cwd=root)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    return root


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


def _git(cwd: Path, *args: str) -> str:
    """git with an identity, and no hooks or signing.

    Isolated from the user and system configuration -- a global ``eol`` or
    attributes file would make the control clone LF and prove nothing --
    except under ``JM_CRLF_HOST_GIT=1``, where that configuration IS the
    subject (gh-1839).
    """
    env = dict(os.environ)
    if not HOST_GIT:
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


def _census(clone: Path) -> str:
    """What *clone* holds and which ``core.autocrlf`` made it, one line.

    For the log: the Windows job runs this file with ``-rP``, so a reader
    sees the CRLF a clone really has rather than taking a green on trust.
    """
    files = [
        p
        for p in clone.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(clone).parts
    ]
    crlf = [p for p in files if b"\r\n" in p.read_bytes()]
    ext = (clone / _EXT).read_bytes()
    cr, lines = ext.count(b"\r"), ext.count(b"\n")
    try:
        got = _git(clone, "config", "--show-origin", "--get", "core.autocrlf")
    except subprocess.CalledProcessError:
        got = "unset"
    mode = "host git" if HOST_GIT else "simulated"
    return (
        f"{clone.name} ({mode}; core.autocrlf {' '.join(got.split())}): "
        f"{len(crlf)} of {len(files)} files CRLF; {_EXT.as_posix()} has "
        f"{cr} CR in {lines} lines"
    )


def _autocrlf_clone(tmp_path: Path, project: Path, name: str) -> Path:
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "scaffold")
    # The host's own default under JM_CRLF_HOST_GIT, Git for Windows'
    # forced otherwise: `-c` for the checkout, `--config` for the clone's
    # own later use.
    forced = ("-c", "core.autocrlf=true")
    _git(
        tmp_path,
        *(() if HOST_GIT else forced),
        "clone",
        "-q",
        *(() if HOST_GIT else ("--config", "core.autocrlf=true")),
        str(project),
        name,
    )
    clone = tmp_path / name
    print(_census(clone))
    return clone


def _clone_without_attributes(tmp_path: Path) -> "tuple[Path, bytes]":
    """An autocrlf clone of a scaffold committed with no ``.gitattributes``.

    The project an older jm made. Returns the clone and the attributes the
    scaffold had, which the caller may put back.
    """
    project = _scaffold(tmp_path)
    attrs = project / ".gitattributes"
    kept = attrs.read_bytes()
    attrs.unlink()
    return _autocrlf_clone(tmp_path, project, "clone"), kept


def _rewritten(tmp_path: Path) -> Path:
    root = _scaffold(tmp_path)
    assert _EXT.as_posix() in _to_crlf(root)
    return root


def _cloned(tmp_path: Path) -> Path:
    """A real CRLF checkout that has since been given its attributes.

    What `jm apply` leaves in an older project's Windows clone: the file
    arrives, and nothing on disk is renormalised until git is asked to.
    """
    clone, attrs = _clone_without_attributes(tmp_path)
    (clone / ".gitattributes").write_bytes(attrs)
    return clone


def _all_crlf(data: bytes) -> bool:
    return data.count(b"\r\n") == data.count(b"\n") > 0


@pytest.fixture(
    params=[
        pytest.param(_rewritten, id="rewritten"),
        pytest.param(_cloned, id="autocrlf-clone", marks=needs_git),
    ]
)
def crlf_project(request, tmp_path: Path) -> Path:
    root = request.param(tmp_path)
    assert _all_crlf((root / _EXT).read_bytes())
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


@needs_git
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

    # gh-1839: and `apply` has nothing to do -- not one byte rewritten,
    # read from the tree rather than from what apply says it did.
    before = _apply._tree_digests(clone)
    r = run_cli("apply", cwd=clone)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _apply._tree_digests(clone) == before, r.stdout


@needs_git
def test_without_the_attributes_an_autocrlf_clone_is_crlf(tmp_path):
    """The control: the clone above is LF BECAUSE of the file, not because
    this git or this host would have checked it out LF anyway. On the
    Windows job it is also what proves the runner's default is autocrlf."""
    clone, _ = _clone_without_attributes(tmp_path)
    assert _all_crlf((clone / _EXT).read_bytes()), _census(clone)


def test_the_windows_job_runs_this_file_against_the_runners_git():
    """gh-1839: the step that makes the clones above real, and nothing else.

    Read from ci.yml as GitHub reads it: the Windows job has exactly one
    step setting ``JM_CRLF_HOST_GIT=1``, and that step runs this file.
    """
    job = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))["jobs"][
        WINDOWS_JOB
    ]
    assert str(job["runs-on"]).startswith("windows"), job["runs-on"]
    steps = [
        s
        for s in job["steps"]
        if str((s.get("env") or {}).get(HOST_GIT_ENV)) == "1"
    ]
    assert len(steps) == 1, (
        f"{WINDOWS_JOB} in {CI_YML.name} must have one step with "
        f"{HOST_GIT_ENV}: '1' -- the only run of this file against a real "
        f"Windows git -- and has {len(steps)}"
    )
    words = shlex.split(steps[0]["run"])
    this = Path(__file__).resolve().relative_to(ROOT).as_posix()
    assert words[:2] == ["make", "test-examples"], words
    assert f"PROJECT_ENV_TESTS={this}" in words, words
