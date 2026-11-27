"""A verb asks `apply`'s questions before it rewrites a module's CMakeLists
(gh-2114).

``native/src/<cname>/CMakeLists.txt`` is the module's own file, and every
verb that adds to or takes from the module -- ``jm object x --module m``,
``jm function``, ``jm method``, ``jm remove object`` -- re-renders it whole
(`_object._regenerate_module_now`). That write never went through `apply`'s
reconcile, so an author's ``add_library(<x> OBJECT ...)`` there was erased
with no word at all, and so was any other statement the author added: not
gh-1840's refusal, not even gh-1351's warning. The root's wiring of the
library was left naming a target that is gone, which cmake rejects at
configure time.

The verb now asks the same two questions through the same code
(`_apply.ask_before_rewriting_cmake`): it refuses an erased OBJECT library,
naming the ``<d>_extra.cmake`` hook, and leaves the tree byte-identical; it
warns of any other dropped statement. A library moved to the hook survives
the verb, and the project builds with it linked.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli, warning_lines

from just_makeit import _libwiring as L

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and default_cc() is not None

#: Every verb that re-renders module `m`'s own CMakeLists, one per writer
#: path into `_regenerate_module_now`: an object added, a function added, a
#: method added to a member, a member removed.
VERBS = {
    "object": ("object", "x", "--module", "m"),
    "function": ("function", "g", "--module", "m"),
    "method": ("method", "w", "run2", "--module", "m"),
    "remove": ("remove", "object", "w", "--force"),
}


@pytest.fixture(scope="module")
def _base(tmp_path_factory) -> Path:
    """Module `m` with its collocated object `m` (which shares the module's
    CMakeLists) and a member `w`."""
    top = tmp_path_factory.mktemp("gh2114")
    r = run_cli("new", "p", cwd=top)
    assert r.returncode == 0, r.stdout + r.stderr
    root = top / "p"
    for args in (
        ("module", "m"),
        ("object", "m", "--module", "m"),
        ("object", "w", "--module", "m"),
        ("apply",),
    ):
        r = run_cli(*args, cwd=root)
        assert r.returncode == 0, (args, r.stdout + r.stderr)
    return root


@pytest.fixture
def project(_base: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "p"
    shutil.copytree(_base, dst)
    return dst


def _cml(root: Path) -> Path:
    return root / "native" / "src" / "m" / "CMakeLists.txt"


def _library(root: Path, where: Path) -> str:
    """The author's OBJECT library `m_helper`, declared in *where* and wired
    into both C libraries from the root."""
    lib = "m_helper"
    (root / "native" / "src" / "m" / f"{lib}.c").write_text(
        f"int {lib}(void) {{ return 2114; }}\n", encoding="utf-8"
    )
    text = where.read_text(encoding="utf-8") if where.is_file() else ""
    where.write_text(
        text + f"\nadd_library({lib} OBJECT {lib}.c)\n"
        f"set_target_properties({lib} PROPERTIES"
        " POSITION_INDEPENDENT_CODE ON)\n",
        encoding="utf-8",
    )
    top = root / "CMakeLists.txt"
    top.write_text(
        top.read_text(encoding="utf-8")
        + "".join(L.wiring_line(t, lib) for t in ("p_lib", "p_lib_static")),
        encoding="utf-8",
    )
    return lib


def _tree(root: Path) -> "dict[str, str]":
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


@pytest.mark.parametrize("verb", VERBS.values(), ids=VERBS.keys())
def test_the_verb_refuses_and_writes_nothing(project: Path, verb):
    """The issue's trigger, on every writer path. On main each exited 0 and
    the library was gone from the file."""
    lib = _library(project, _cml(project))
    before = _tree(project)
    r = run_cli(*verb, cwd=project)
    assert r.returncode == 1, r.stdout + r.stderr
    assert (
        f"native/src/m/CMakeLists.txt: {lib} -> native/src/m/m_extra.cmake"
    ) in r.stderr, r.stderr
    assert _tree(project) == before


@pytest.mark.parametrize("verb", VERBS.values(), ids=VERBS.keys())
def test_any_other_dropped_statement_is_named(project: Path, verb):
    """gh-1351's warning, which only `apply` printed: the verb still writes,
    as `apply` does, and says what it drops and where it belongs."""
    cml = _cml(project)
    cml.write_text(
        cml.read_text(encoding="utf-8")
        + "\ntarget_compile_definitions(m_core PRIVATE P_GH2114=1)\n",
        encoding="utf-8",
    )
    r = run_cli(*verb, cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    (line,) = [
        w
        for w in warning_lines(r.stdout + r.stderr)
        if "target_compile_definitions()" in w
    ]
    assert "native/src/m/m_extra.cmake" in line, line


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="cmake or a C compiler")
def test_a_library_in_the_hook_survives_and_builds(project: Path):
    """Where the refusal sends it: the hook. The verb leaves it alone, and
    the project configures and builds with the library in lib<pkg>."""
    hook = project / "native" / "src" / "m" / "m_extra.cmake"
    lib = _library(project, hook)
    kept = hook.read_bytes()
    r = run_cli(*VERBS["object"], cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert hook.read_bytes() == kept
    assert not warning_lines(r.stdout + r.stderr), r.stdout + r.stderr
    build = project / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(project),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build), "-j", "4"],
    ):
        r2 = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        assert r2.returncode == 0, r2.stdout[-3000:] + r2.stderr[-3000:]
    libs = [
        p
        for p in build.rglob("libp*")
        if p.is_file() and p.suffix in (".so", ".a", ".dylib")
    ]
    assert libs, sorted(p.name for p in build.rglob("libp*"))
    assert all(lib.encode() in p.read_bytes() for p in libs), libs
