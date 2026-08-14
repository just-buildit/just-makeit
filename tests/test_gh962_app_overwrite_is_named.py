"""gh-962: losing an edit to a generated app must not be silent.

`native/src/app/<name>.c` is regenerated wholesale — by `jm app`, and by every
`jm apply`, because the replay re-runs the verb from `[app]`. Nothing preserves
a body there the way `_restore_c_function_bodies` preserves `_core.c`, and an
app has no `_extra.c` escape hatch. So an edit is simply gone.

**And the file invited the edit.** Its own generated banner read:

    // Re-running `just-makeit app` overwrites this file; edit for custom logic.

Consistent on its own — it names the command to avoid — and wrong about the one
that matters, because `jm apply` is the command a project runs routinely and CI
runs as a drift gate.

What jm already did right, and what it did not: `jm status` reported the file
as STALE beforehand, which is a real warning shot. But its footer reads "your
`_core.c` is kept", which reassures about a different file, and `jm apply`
itself printed only `update` — no warning on stderr at all.

Reported rather than refused. Refusing would leave a stale app that no command
could refresh, and reconciling is `apply`'s whole contract; the author is told
what was discarded and where the logic belongs instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

SRC = Path(__file__).parent.parent / "src"
TEMPLATES = SRC / "just_makeit" / "templates"


def _cli(*args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from just_makeit._cli import main; main()",
            *args,
        ],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture()
def app_project(tmp_path):
    assert _cli("new", "p", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    assert _cli("object", "gain", cwd=proj).returncode == 0
    r = _cli("app", "--name", "runner", cwd=proj)
    assert r.returncode == 0, r.stderr
    return proj


def _main_c(proj: Path) -> Path:
    return proj / "native/src/app/runner.c"


def test_an_edited_app_is_named_when_apply_discards_it(app_project):
    """The defect: the loss happens, and now it is said out loud.

    Sabotage: drop the `_report.warn` in `_app._write_c_app` and this fails
    while every other assertion here still passes.
    """
    f = _main_c(app_project)
    f.write_text(
        f.read_text(encoding="utf-8") + "\n/* MY_AUTHORED_LOGIC */\n",
        encoding="utf-8",
    )
    r = _cli("apply", cwd=app_project)
    assert r.returncode == 0
    assert "MY_AUTHORED_LOGIC" not in f.read_text(encoding="utf-8"), (
        "fixture assumes apply regenerates the app; it no longer does"
    )
    assert "your edits to it have just been discarded" in r.stderr, (
        f"apply discarded an edit to the app silently:\n{r.stderr}"
    )
    assert "native/src/app/runner.c" in r.stderr


def test_an_untouched_app_says_nothing(app_project):
    """Most re-runs are over an unedited scaffold; those must stay quiet.

    A warning that fires on every `jm apply` is one nobody reads, which would
    put this straight back where it started.
    """
    r = _cli("apply", cwd=app_project)
    assert r.returncode == 0
    assert "discarded" not in r.stderr, r.stderr


def test_the_first_scaffold_says_create_not_update(app_project, tmp_path):
    """A second gh-962 bug, found while reading the write site.

    `verb = "update" if main_c.exists()` was evaluated AFTER the write, so
    `exists()` was trivially true and a brand-new file announced itself as an
    update.
    """
    assert _cli("new", "q", cwd=tmp_path).returncode == 0
    proj = tmp_path / "q"
    assert _cli("object", "gain", cwd=proj).returncode == 0
    r = _cli("app", "--name", "fresh", cwd=proj)
    assert "create" in r.stdout and "fresh.c" in r.stdout, r.stdout
    # ...and a re-run over the existing file is an update.
    again = _cli("app", "--name", "fresh", cwd=proj)
    assert "update" in again.stdout and "fresh.c" in again.stdout


def test_no_app_template_invites_an_edit_it_cannot_keep():
    """Derived over the templates, so a new app target cannot reintroduce it.

    The banner is the whole reason gh-962 was a trap rather than a footnote:
    it told the author to edit a file jm regenerates, and named only one of
    the two commands that do it. Checking every `app_*` template rather than
    the six that existed means a seventh target inherits the rule.
    """
    offenders = []
    for f in sorted(TEMPLATES.rglob("app_*")):
        text = f.read_text(encoding="utf-8")
        head = "\n".join(text.splitlines()[:12])
        if "edit for custom logic" in head or "fill each command body" in head:
            offenders.append(f"{f.name}: invites an edit that is discarded")
        elif "just-makeit app" in head and "apply" not in head:
            offenders.append(f"{f.name}: names `jm app` but not `jm apply`")
    assert not offenders, offenders
