"""gh-2792: two shapes `_ctorsig.why_drift` handles that no test pinned.

Follow-ups from the review of #2790 (gh-2588, gh-2788), which made
``why_drift`` read the author's definitions -- the header and every ``.c``
in the component's source directory -- as well as the declaration.

1. **A collocated module object** (one named for its module) shares the
   module's source directory, so that directory's ``<mod>_ext.c`` and every
   object's ``<mod>_ext_<obj>.c`` are read too. Those fragments CALL the
   method with ``&_why``; a call is not a definition, so a project whose C
   matches its manifest has no WHY finding. A future reader that took a
   call for a definition would report one on every such project.
2. **A missing header.** ``_ctorsig._read`` answers ``""`` for a file it
   cannot read, so a component whose header is gone (deleted after
   scaffolding) gives no finding rather than a crash.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from _jmrun import run_cli


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _module_project(tmp_path: Path) -> Path:
    """Module ``dsp`` with the collocated object ``dsp`` and a member ``two``,
    each with a ``why`` method -- so ``native/src/dsp/`` holds both
    objects' binding fragments, each calling its kernel with ``&_why``."""
    _ok("new", "sq", cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("module", "dsp", cwd=proj)
    for obj in ("dsp", "two"):
        _ok("object", obj, "--module", "dsp", cwd=proj)
        _ok(
            "method", obj, "stat", "--module", "dsp", "--param", "mode:int",
            "--return-type", "int", "--status-return", "--why", cwd=proj,
        )  # fmt: skip
    src = proj / "native" / "src" / "dsp"
    # The premise: the fragments that call the method are in the directory
    # `why_drift` reads for the collocated object.
    calls = [
        p.name
        for p in src.glob("*.c")
        if "sq_dsp_stat(" in p.read_text(encoding="utf-8")
    ]
    assert "dsp_ext_dsp.c" in calls, sorted(p.name for p in src.glob("*.c"))
    return proj


def test_a_collocated_objects_binding_calls_are_not_definitions(tmp_path):
    proj = _module_project(tmp_path)
    r = run_cli("status", "--check", cwd=proj)
    out = r.stdout + r.stderr
    assert "WHY" not in out, out
    assert r.returncode == 0, out


def test_a_collocated_objects_stale_definition_is_still_reported(tmp_path):
    """The over-fix guard: the real drift, in the same directory, is found."""
    proj = _module_project(tmp_path)
    core_c = proj / "native" / "src" / "dsp" / "dsp_core.c"
    text = core_c.read_text(encoding="utf-8")
    old = "sq_dsp_stat(sq_dsp_state_t *state, int mode, const char **why)"
    assert text.count(old) == 1, text
    core_c.write_text(
        text.replace(old, "sq_dsp_stat(sq_dsp_state_t *state, int mode)"),
        encoding="utf-8",
    )
    r = run_cli("status", "--check", cwd=proj)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "native/src/dsp/dsp_core.c" in out and "defined:" in out, out
    # Exactly the one stale spelling: no fragment is reported beside it.
    assert "dsp_ext" not in out.split("WHY", 1)[1].split("\n\n", 1)[0], out


def test_a_missing_header_is_no_finding_and_no_crash(tmp_path):
    from just_makeit import _config as C
    from just_makeit import _ctorsig
    from just_makeit import _incpath as INC

    _ok("new", "sq", cwd=tmp_path)
    proj = tmp_path / "sq"
    _ok("object", "cor", cwd=proj)
    _ok(
        "method", "cor", "stat", "--param", "mode:int", "--return-type",
        "int", "--status-return", "--why", cwd=proj,
    )  # fmt: skip
    # The manifest's side: the tree as rendered, header included.
    tree = tmp_path / "tree"
    shutil.copytree(proj, tree)
    cfg = C.load(tree)
    header = INC.core_h(proj, "cor", cfg)
    assert header.is_file()
    header.unlink()
    assert _ctorsig.why_drift(proj, tree, cfg) == []
