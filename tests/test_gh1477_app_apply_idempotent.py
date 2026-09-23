"""gh-1477: a second `apply` on an unchanged ``[app]`` project writes nothing.

Found by gh-1443's Gate A on its first run: every example declaring an
``[app]`` announced the app source, the manifest and the root CMakeLists
(or ``pyproject.toml``) as ``update`` on every apply. Two causes, one of
them real bytes:

* **The replay reordered the manifest's own lists.** `apply` re-runs the
  ``jm app`` verb from ``[app]`` and hands it the manifest's own
  ``[[app.commands]]`` / ``[[app.flags]]`` list. ``add_app_command`` and
  ``add_app_flag`` rewrite that list in place (``lst[:] = ...``), so the
  loop re-appended entries while walking them and reversed their order --
  a real rewrite of a multi-command app's dispatch, announced as edits
  "just discarded".
* **The replay reported outside the byte-derived report.** It ran after
  apply's summary and printed its own unconditional ``update`` lines, so
  even a byte-identical rewrite (the C app, the CMake splice, the manifest)
  was announced. It now runs before gh-1474's report and is judged by it.

Every app shape is scaffolded through the real CLI, applied once, and then
applied again; the second must print nothing and change no byte -- the
manifest included, which `apply`'s own report never walks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _jmrun import run_cli

_NOTHING = "Project already matches just-makeit.toml — nothing to do."


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, f"jm {' '.join(args)}\n{r.stdout}\n{r.stderr}"
    return r.stdout + r.stderr


def _snap(proj: Path) -> dict:
    return {
        p.relative_to(proj).as_posix(): p.read_bytes()
        for p in proj.rglob("*")
        if p.is_file() and "build" not in p.relative_to(proj).parts
    }


def _object_project(tmp_path: Path) -> Path:
    _ok(
        "new",
        "tool",
        "--object",
        "gain",
        "--state",
        "gain:float:1.0f",
        "--arg-type",
        "float",
        "--return-type",
        "float",
        cwd=tmp_path,
    )
    return tmp_path / "tool"


# Each shape's `jm app` arguments. Two entries in every list the replay
# re-declares, since a one-element list cannot be reordered.
_SHAPES = {
    "c-object-flags": [
        "--target",
        "c",
        "--object",
        "gain",
        "--name",
        "gaintool",
        "--flag",
        "alpha:int32_t:1",
        "--flag",
        "beta:int32_t:2",
    ],
    "console-object-flags": [
        "--target",
        "console",
        "--object",
        "gain",
        "--name",
        "gaintool",
        "--flag",
        "alpha:int32_t:1",
        "--flag",
        "beta:int32_t:2",
    ],
    "pep723-object": [
        "--target",
        "pep723",
        "--object",
        "gain",
        "--name",
        "gaintool",
    ],
    "c-commands": [
        "--target",
        "c",
        "--name",
        "cmdtool",
        "--command",
        "encode:encode input",
        "--command",
        "info:print info",
    ],
    "console-commands": [
        "--target",
        "console",
        "--name",
        "cmdtool",
        "--command",
        "encode:encode input",
        "--command",
        "info:print info",
    ],
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_second_apply_on_an_app_project_writes_nothing(tmp_path, shape):
    proj = _object_project(tmp_path)
    _ok("app", *_SHAPES[shape], cwd=proj)
    _ok("apply", cwd=proj)
    before = _snap(proj)

    out = _ok("apply", cwd=proj)

    after = _snap(proj)
    changed = sorted(
        k
        for k in before.keys() | after.keys()
        if before.get(k) != after.get(k)
    )
    assert changed == [], f"second apply rewrote {changed}\n{out}"
    assert _NOTHING in out, out
    # Anchored on the report's own line shape, so a path merely MENTIONED
    # in prose cannot satisfy or defeat it.
    writes = [
        ln
        for ln in out.splitlines()
        if ln.lstrip().startswith(("create  ", "update  "))
    ]
    assert writes == [], out
    assert "discarded" not in out, out


def test_the_replay_keeps_the_declared_order(tmp_path):
    """The real-bytes half: the manifest's `[[app.commands]]` order survives
    any number of applies, and so does the dispatch generated from it."""
    proj = _object_project(tmp_path)
    _ok("app", *_SHAPES["c-commands"], cwd=proj)
    app_c = proj / "native" / "src" / "app" / "cmdtool.c"
    first = app_c.read_text(encoding="utf-8")
    for _ in range(3):
        _ok("apply", cwd=proj)
        assert app_c.read_text(encoding="utf-8") == first
    manifest = (proj / "just-makeit.toml").read_text(encoding="utf-8")
    assert manifest.index('name = "encode"') < manifest.index('name = "info"')


def test_a_real_app_change_is_still_reported(tmp_path):
    """The other direction: an edited app source is restored AND reported,
    with the warning that says the edit was discarded."""
    proj = _object_project(tmp_path)
    _ok("app", *_SHAPES["c-object-flags"], cwd=proj)
    _ok("apply", cwd=proj)
    app_c = proj / "native" / "src" / "app" / "gaintool.c"
    pristine = app_c.read_text(encoding="utf-8")
    app_c.write_text(pristine + "/* local edit */\n", encoding="utf-8")

    out = _ok("apply", cwd=proj)

    assert app_c.read_text(encoding="utf-8") == pristine
    assert "  update  native/src/app/gaintool.c" in out.splitlines(), out
    assert "discarded" in out, out
