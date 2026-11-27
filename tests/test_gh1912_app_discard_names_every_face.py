"""gh-1912: an edit a `jm apply` discards is named on every app face.

`jm app` and every `jm apply` regenerate an app's source wholesale, so an
author's edit is gone after the next apply. gh-962 made the C face say so, in
`_run_c`, and left the console and pep723 faces overwriting in silence. The
check now lives in `_app._write_app`, the one path all three write through, and
this gate runs the same two cases over each face: an edited file is named, and
an untouched one says nothing.

Each case drives jm through `run_cli`: `new`, `app`, the author's edit, `apply`.
"""

import pytest

from _jmrun import run_cli

#: (face, `jm app` arguments, the file the face generates, relative to root)
FACES = [
    ("c", ["--name", "runner"], "native/src/app/runner.c"),
    ("console", ["--target", "console", "--name", "t"], "src/p/cli.py"),
    (
        "pep723",
        ["--target", "pep723", "--object", "g", "--name", "t"],
        "t.py",
    ),
]


@pytest.fixture()
def app(tmp_path, request):
    face, app_args, rel = request.param
    assert run_cli("new", "p", "--object", "g", cwd=tmp_path).returncode == 0
    proj = tmp_path / "p"
    r = run_cli("app", *app_args, cwd=proj)
    assert r.returncode == 0, r.stderr
    return face, proj, proj / rel


@pytest.mark.parametrize(
    "app", FACES, indirect=True, ids=[f[0] for f in FACES]
)
def test_an_edit_the_apply_discards_is_named(app):
    """Sabotage: drop the `_report.warn` in `_app._write_app` and this fails on
    every face at once, while the untouched-run test below still passes."""
    face, proj, f = app
    assert f.exists(), f"{face}: the scaffold did not write {f}"
    f.write_text(f.read_text(encoding="utf-8") + "\n# MY_AUTHORED_LOGIC\n",
                 encoding="utf-8")  # fmt: skip
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "MY_AUTHORED_LOGIC" not in f.read_text(encoding="utf-8"), (
        f"{face}: the fixture assumes apply regenerates the file"
    )
    assert "your edits to it have just been discarded" in r.stderr, (
        f"{face}: apply discarded an edit and said nothing:\n{r.stderr}"
    )
    assert str(f) in r.stderr, f"{face}: the warning does not name {f}"


@pytest.mark.parametrize(
    "app", FACES, indirect=True, ids=[f[0] for f in FACES]
)
def test_an_untouched_app_says_nothing(app):
    """A warning on every apply is one nobody reads: a re-run over an unedited
    scaffold must stay quiet on every face."""
    face, proj, _f = app
    r = run_cli("apply", cwd=proj)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "discarded" not in r.stderr, f"{face}: {r.stderr}"
