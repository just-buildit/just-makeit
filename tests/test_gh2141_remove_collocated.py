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
    _ok("object", "filt", "--module", "filt", cwd=proj)
    if function:
        # TEMPORARY order, not the intended shape: the natural one (the
        # function first, then the collocated object) leaves the header
        # without the function's declaration before any removal -- #2843
        # item 1, fixed in this same wave. That fix restores the natural
        # order here.
        _ok("function", "gain", "--module", "filt", cwd=proj)
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
    # the C test and bench are its core's, not the object's. The link table
    # (gh-1361) is the object's alone -- a functions-only module has none,
    # and nothing compiles it once the object is gone -- so it goes.
    proj = _collocated(tmp_path, function=True)
    test_c, symbols_c, bench_c = _harness(proj)
    assert all(p.is_file() for p in (test_c, symbols_c, bench_c))
    out = _ok("remove", "object", "filt", "--force", cwd=proj)
    assert test_c.is_file() and bench_c.is_file()
    assert not symbols_c.exists()
    # The kept two still call the removed object's create() and step(), so
    # the note says so: deleting those bodies alone breaks both builds.
    assert (
        "native/tests/test_filt_core.c, native/benchmarks/bench_filt_core.c"
        " stay as the module's C test and bench, and still call 'filt''s"
        " create() and step()"
    ) in out, out
    status = run_cli("status", "--check", cwd=proj)
    assert status.returncode == 0, status.stdout + status.stderr


def test_the_removal_names_what_the_author_still_owns(tmp_path):
    # The kept core still holds the object's C, and only the author may
    # delete it: the closing note is the one place that says so.
    proj = _collocated(tmp_path)
    out = _ok("remove", "object", "filt", "--force", cwd=proj)
    assert (
        "note: native/inc/p/filt/filt_core.h and its _core.c are module"
        " 'filt''s core and stay; delete 'filt''s declarations and bodies"
        " from them by hand."
    ) in out, out
    # No functions, so no kept C test or bench for a second note to name.
    assert "stay as the module's C test and bench" not in out, out


def test_the_prompt_does_not_threaten_the_kept_core(tmp_path):
    # The core is kept, so "will be permanently deleted" and "cannot be
    # recovered without git" -- the prompt for any other object whose
    # core holds the author's C -- would both be false here.
    proj = _collocated(tmp_path)
    core_h, _ = _core(proj)
    # The author implements step(): the stub's marker goes, which is what
    # `_remove._is_implemented` reads as hand-written code.
    text = core_h.read_text(encoding="utf-8")
    stub = "/* TODO: implement using state variables */"
    assert text.count(stub) == 1
    core_h.write_text(
        text.replace(stub, "/* the author's step */"), encoding="utf-8"
    )
    r = run_cli("remove", "object", "filt", cwd=proj, stdin="n\n")
    out = r.stdout + r.stderr
    assert "Aborted." in out, out
    assert "permanently deleted" not in out, out
    assert "cannot be recovered" not in out, out
    assert "core and are kept." in out, out


def test_a_dependent_takes_the_core_wiring_apply_gives_it(tmp_path):
    # While the collocated object stands, the module's block folds its
    # core in and a member that depends_on it adds no line of its own.
    # Once it is removed, `apply` wires that core with the dependent: the
    # removal must leave the root CMakeLists exactly there.
    proj = _collocated(tmp_path)
    _ok("object", "biq", "--module", "filt", cwd=proj)
    frag = proj / "objects" / "biq.toml"
    text = frag.read_text(encoding="utf-8")
    assert text.count("[biq]\n") == 1
    frag.write_text(
        text.replace("[biq]\n", '[biq]\ndepends_on = ["filt"]\n'),
        encoding="utf-8",
    )
    _ok("apply", cwd=proj)
    _ok("remove", "object", "filt", "--force", cwd=proj)
    status = run_cli("status", "--check", cwd=proj)
    assert status.returncode == 0, status.stdout + status.stderr


def _build(proj: Path) -> Path:
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
    return build


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
def test_the_module_builds_after_the_removal(tmp_path):
    proj = _collocated(tmp_path)
    _ok("remove", "object", "filt", "--force", cwd=proj)
    build = _build(proj)
    # The author's definition was compiled into the module core: it is in
    # the object file cmake built from the surviving `filt_core.c`.
    objs = list(build.rglob("filt_core.c.o*"))
    assert objs, sorted(p.name for p in build.rglob("*.o*"))
    assert any(b"author_kept_gh2141" in o.read_bytes() for o in objs)


# The verbs that rebuild the collocated object's core rather than remove
# it. Each goes through `_regenerate`, which lists the object's paths with
# `_remove._object_paths` -- the list gh-2141 taught to keep the module's
# core. Kept there too, the rebuilt header declared a `create()` the old
# `_core.c` did not define: `status --check` stayed 0 and the module no
# longer compiled. Only a build sees that, so this gate builds.
_REBUILDS = {
    "add": ("add", "--state", "bias:double:1.0", "--object", "filt", "-f"),
    "remove-state": ("remove", "state", "gain", "--object", "filt", "-f"),
    "regenerate": ("regenerate", "filt", "--force"),
}


@pytest.mark.skipif(bool(_SKIP), reason=_SKIP or "")
@pytest.mark.parametrize("verb", sorted(_REBUILDS))
def test_a_rebuild_of_the_collocated_object_still_builds(tmp_path, verb):
    _ok("new", "p", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("module", "filt", cwd=proj)
    _ok("object", "filt", "--module", "filt", cwd=proj)
    _ok("object", "biq", "--module", "filt", cwd=proj)
    if verb == "regenerate":
        frag = proj / "objects" / "filt.toml"
        with frag.open("a", encoding="utf-8") as f:
            f.write(
                '\n[[filt.state]]\nname = "bias"\ntype = "double"\n'
                'default = "1.0"\n'
            )
    _ok(*_REBUILDS[verb], cwd=proj)
    _build(proj)
    status = run_cli("status", "--check", cwd=proj)
    assert status.returncode == 0, status.stdout + status.stderr
