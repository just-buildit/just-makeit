"""gh-2141: removing a module's collocated object leaves the module its core.

The collocated object is the one named for its module's cname
(`_config.collocated_object`). It shares the module's own native
directories: ``native/src/<cname>/`` holds the module's CMakeLists and
aggregator, and ``<cname>_core.h`` / ``<cname>_core.c`` are the module's
core as much as the object's. ``jm remove object`` deleted both directories
whole, then wrote the module's CMakeLists and ``_ext.c`` back into an
emptied one, so `status --check` reported the core MISSING and cmake could
not configure.

Removing it now takes only what is the object's own (its binding fragment,
its tests, its benches) and leaves the core in place with the author's C in
it. The gate builds the tree after the removal and reads the author's code
back out of the files that survived.
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

# The author's own C, in the core the module and the object share: a
# definition in the source and its declaration in the header. Neither is a
# name jm derives, so neither is a symbol a removal could claim as its own.
_DECL = "int author_kept_gh2141(void);\n"
_DEF = "\nint author_kept_gh2141(void) { return 2141; }\n"


def _ok(*args: str, cwd: Path) -> str:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _collocated(tmp_path: Path, *, function: bool = False) -> Path:
    """Project ``p``, module ``filt`` and its collocated object ``filt``,
    the author's C added to the shared core."""
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "filt", cwd=proj)
    if function:
        _ok("function", "gain", "--module", "filt", cwd=proj)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    core_h, core_c = _core(proj)
    text = core_h.read_text(encoding="utf-8")
    guard = text.rindex("#endif")
    core_h.write_text(text[:guard] + _DECL + text[guard:], encoding="utf-8")
    with core_c.open("a", encoding="utf-8") as f:
        f.write(_DEF)
    return proj


def _core(proj: Path) -> "tuple[Path, Path]":
    return (
        proj / "native" / "inc" / "p" / "filt" / "filt_core.h",
        proj / "native" / "src" / "filt" / "filt_core.c",
    )


def _harness(proj: Path) -> "list[Path]":
    native = proj / "native"
    return [
        native / "tests" / "test_filt_core.c",
        native / "tests" / "test_filt_symbols.c",
        native / "benchmarks" / "bench_filt_core.c",
    ]


def test_the_module_keeps_its_core_and_the_authors_c(tmp_path):
    proj = _collocated(tmp_path)
    fragment = proj / "native" / "src" / "filt" / "filt_ext_filt.c"
    assert fragment.is_file()  # the premise: the object HAS its own file
    _ok("remove", "object", "filt", "--force", cwd=proj)
    core_h, core_c = _core(proj)
    assert _DECL in core_h.read_text(encoding="utf-8")
    assert _DEF in core_c.read_text(encoding="utf-8")
    # What was the object's own is gone: its binding fragment, and (the
    # module having no functions) the C harness that exercised its core.
    assert not fragment.exists()
    assert not any(p.exists() for p in _harness(proj))
    status = run_cli("status", "--check", cwd=proj)
    assert status.returncode == 0, status.stdout + status.stderr


def test_a_module_with_functions_keeps_its_core_harness(tmp_path):
    # gh-1479's keep, from the other side: while the module has functions
    # the C test, link table and bench are its core's, not the object's.
    proj = _collocated(tmp_path, function=True)
    assert all(p.is_file() for p in _harness(proj))
    _ok("remove", "object", "filt", "--force", cwd=proj)
    assert all(p.is_file() for p in _harness(proj)), [
        p.name for p in _harness(proj) if not p.exists()
    ]


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_the_module_builds_after_the_removal(tmp_path):
    proj = _collocated(tmp_path)
    _ok("remove", "object", "filt", "--force", cwd=proj)
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
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    # The author's definition was compiled into the module core: it is in
    # the object file cmake built from the surviving `filt_core.c`.
    objs = list(build.rglob("filt_core.c.o*"))
    assert objs, sorted(p.name for p in build.rglob("*.o*"))
    assert any(b"author_kept_gh2141" in o.read_bytes() for o in objs)
