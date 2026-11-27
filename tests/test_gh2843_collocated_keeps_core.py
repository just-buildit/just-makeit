"""gh-2843 item 1: a collocated object keeps what its module's core held.

``jm object m --module m`` creates the object named for its module, whose
core IS the module's own ``m_core.h`` / ``m_core.c``. The verb rendered both
over whatever the module had put there:

- the header lost the declarations ``jm function`` had injected, so
  ``m_ext.c`` called a function nothing declared, and ``status --check``
  reported the header STALE on a tree the verb had just written;
- ``_core.c`` lost the author's own functions, with no prompt.

Now the header is the object's render plus everything the module's held
(declarations, inline definitions, includes), and ``_core.c`` is only ever
appended to. Code jm cannot place is refused, with the tree unchanged.
The gate builds each tree and runs its C tests.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    return None


_SKIP = _skip_reason()


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _module(tmp_path: Path) -> Path:
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "filt", cwd=proj)
    return proj


def _core(proj: Path) -> "tuple[Path, Path]":
    return (
        proj / "native" / "inc" / "p" / "filt" / "filt_core.h",
        proj / "native" / "src" / "filt" / "filt_core.c",
    )


def _status_ok(proj: Path) -> None:
    s = run_cli("status", "--check", cwd=proj)
    assert s.returncode == 0, s.stdout + s.stderr


def _build_and_test(proj: Path) -> None:
    build = proj / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(proj),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build), "-j", "4"],
        ["ctest", "--test-dir", str(build), "--output-on-failure"],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]


@pytest.mark.parametrize("inline", [False, True], ids=["extern", "inline"])
def test_the_header_keeps_the_modules_functions(tmp_path, inline):
    # The natural order: the module's function first, then the object.
    proj = _module(tmp_path)
    fn = ["function", "gain", "--module", "filt", "--param", "x:double"]
    _ok(
        *fn,
        "--return-type",
        "double",
        *(["--inline"] if inline else []),
        cwd=proj,
    )
    core_h, _ = _core(proj)
    before = core_h.read_text(encoding="utf-8")
    assert "p_gain" in before  # the premise: the module declared it
    _ok("object", "filt", "--module", "filt", cwd=proj)
    after = core_h.read_text(encoding="utf-8")
    assert "p_filt_create" in after  # the object's render is there...
    want = "p_gain(double x)" + ("" if inline else ";")
    assert want in after, after  # ...and so is the module's function
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


_AUTHOR_C = "\nint p_author_gh2843(void) { return 2843; }\n"


def test_core_c_keeps_the_authors_code(tmp_path):
    proj = _module(tmp_path)
    _, core_c = _core(proj)
    with core_c.open("a", encoding="utf-8") as f:
        f.write(_AUTHOR_C)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    text = core_c.read_text(encoding="utf-8")
    assert _AUTHOR_C.strip() in text, text
    # Every definition the object needs was appended, including the
    # one-line `void p_filt_steps(` signature the house-style reader skips.
    for name in ("p_filt_create", "p_filt_destroy", "p_filt_steps"):
        assert f"{name}(" in text, name
    _status_ok(proj)
    if not _SKIP:
        _build_and_test(proj)


def test_code_jm_cannot_place_is_refused(tmp_path):
    proj = _module(tmp_path)
    core_h, core_c = _core(proj)
    text = core_h.read_text(encoding="utf-8")
    inc = '#include "p/clib_common.h"\n'
    assert text.count(inc) == 1
    core_h.write_text(
        text.replace(inc, inc + "#define FILT_TAPS 8\n"), encoding="utf-8"
    )
    tree = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    r = run_cli("object", "filt", "--module", "filt", cwd=proj)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "#define FILT_TAPS 8" in r.stderr, r.stderr
    after = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    assert after == tree


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_a_plain_collocated_object_still_builds(tmp_path):
    proj = _module(tmp_path)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _status_ok(proj)
    _build_and_test(proj)
