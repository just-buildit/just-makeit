"""An author's OBJECT library in a CMakeLists `apply` regenerates (gh-1840).

``native/src/<d>/CMakeLists.txt`` is glue: ``jm apply`` rewrites it from the
manifest. An ``add_library(<x> OBJECT ...)`` an author added there was erased
by the next apply, and nothing said so -- gh-1351's warning names a dropped
command only when jm never writes that command, and jm writes
``add_library``. The root's wiring of the library was left naming a target
that is gone, which cmake rejects at configure time, and the apply after
that deleted the wiring too. Meanwhile `status` told the author "`jm apply`
writes the missing target_sources() line" for the library apply was about
to erase: its "apply wires it" was "the replay's apply left it not unwired",
and a core that is gone is not unwired.

By the precedent of gh-1448's refusal (rendering a file whole would lose
the author's code: refuse, write nothing, name the ``_extra.c`` hook) and
gh-1351's hook (a directory's own CMake lives in ``<d>_extra.cmake``):

- `apply` refuses before its first write, naming each library and the hook
  it belongs in. A library the rewrite KEEPS is not refused -- a preserved
  ``if()`` block in a reconciled file, a file `status_allow` names -- and a
  module object's CMakeLists now honours `status_allow` like its peers.
- `_libwiring` reads the hook, so a library moved there is seen: its root
  wiring survives `apply` (it was deleted as DANGLING), the project links
  it, and UNWIRED reports it when nothing wires it.
- UNWIRED's "apply writes it" is read from what the replay's apply left: a
  core the scratch still declares and no longer reports unwired.

Formatter layouts (gh-1459's emulation of cmake-format's) are held at the
reader, where they cost nothing, and once end to end on the hook.

GATE: an author OBJECT library in a regenerated component CMakeLists is
      refused with the tree byte-identical; moved to the hook it survives
      `apply`, links into lib<pkg>, and `status --check` is clean; and
      `status` never promises an apply that would erase it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _compilers import default_cc
from _jmrun import run_cli
from test_gh1459_root_cmake_findings import _dangle, _upper, _vertical

from just_makeit import _libwiring as L

_HAVE_TOOLCHAIN = bool(shutil.which("cmake")) and default_cc() is not None

#: What the old advice said, for a library apply was about to erase.
PROMISE = "writes the missing target_sources() line"


def _plain(text: str) -> str:
    return text


#: cmake-format's other layouts, as gh-1459 emulates them.
LAYOUTS = [_plain, _vertical, _upper, _dangle]


@pytest.fixture(scope="module")
def _base(tmp_path_factory) -> Path:
    """A standalone object `o`, a module `m` with a collocated object `m`
    (which shares the module's CMakeLists) and a separate one `w`."""
    top = tmp_path_factory.mktemp("gh1840")
    r = run_cli("new", "p", "--object", "o", cwd=top)
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
    r = run_cli("status", "--check", cwd=root)
    assert r.returncode == 0, r.stdout
    return root


@pytest.fixture
def project(_base: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "p"
    shutil.copytree(_base, dst)
    return dst


def _block(lib: str) -> str:
    """The author's library: a target and the statement configuring it."""
    return (
        f"add_library({lib} OBJECT {lib}.c)\n"
        f"set_target_properties({lib} PROPERTIES"
        " POSITION_INDEPENDENT_CODE ON)\n"
    )


def _source(root: Path, d: str) -> str:
    lib = f"{d}_helper"
    (root / "native" / "src" / d / f"{lib}.c").write_text(
        f"int {lib}(void) {{ return 42; }}\n", encoding="utf-8"
    )
    return lib


def _append(path: Path, text: str) -> None:
    path.write_text(
        path.read_text(encoding="utf-8") + "\n" + text, encoding="utf-8"
    )


def _in_cmakelists(root: Path, d: str) -> str:
    """The issue's trigger: the library in the generated CMakeLists."""
    lib = _source(root, d)
    _append(root / "native" / "src" / d / "CMakeLists.txt", _block(lib))
    return lib


def _in_hook(root: Path, d: str, layout=_plain) -> str:
    """Where the refusal sends it: the directory's `_extra.cmake`."""
    lib = _source(root, d)
    (root / "native" / "src" / d / f"{d}_extra.cmake").write_text(
        layout(_block(lib)), encoding="utf-8"
    )
    return lib


def _wire(root: Path, lib: str) -> "list[str]":
    """Fold *lib* into both C libraries from the root, past jm's blocks."""
    lines = [L.wiring_line(t, lib) for t in ("p_lib", "p_lib_static")]
    _append(root / "CMakeLists.txt", "".join(lines))
    return lines


def _tree(root: Path) -> "dict[str, str]":
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _allow(root: Path, *rels: str) -> None:
    m = root / "just-makeit.toml"
    pats = ", ".join(f'"{r}"' for r in rels)
    text = m.read_text(encoding="utf-8")
    assert "status_allow" not in text
    m.write_text(
        text.replace(
            "[project]\n", f"[project]\nstatus_allow = [{pats}]\n", 1
        ),
        encoding="utf-8",
    )


# ── apply refuses, and writes nothing ───────────────────────────────────────


@pytest.mark.parametrize(
    "d", ["o", "m", "w"], ids=["standalone", "module-own", "module-object"]
)
def test_apply_refuses_and_writes_nothing(project: Path, d: str):
    """Every kind of component CMakeLists, reconciled (`o`, `w`) and
    overwritten (`m`). On main the library was erased and apply exited 0."""
    lib = _in_cmakelists(project, d)
    _wire(project, lib)
    before = _tree(project)
    r = run_cli("apply", cwd=project)
    assert r.returncode == 1, r.stdout + r.stderr
    assert (
        f"native/src/{d}/CMakeLists.txt: {lib} -> "
        f"native/src/{d}/{d}_extra.cmake"
    ) in r.stderr, r.stderr
    assert _tree(project) == before


@pytest.mark.parametrize(
    "d, kept", [("o", True), ("m", False)], ids=["reconciled", "overwritten"]
)
def test_only_a_library_the_rewrite_drops_is_refused(
    project: Path, d: str, kept: bool
):
    """An `if()` block carrying link wiring survives the reconcile (gh-271),
    so a library in one is not refused there -- and the module's own file is
    overwritten whole, so the same block there is."""
    lib = _source(project, d)
    cml = project / "native" / "src" / d / "CMakeLists.txt"
    _append(
        cml,
        "if(P_WITH_HELPER)\n"
        f"  add_library({lib} OBJECT {lib}.c)\n"
        f"  target_link_libraries({lib} PRIVATE m)\n"
        "endif()\n",
    )
    before = _tree(project)
    r = run_cli("apply", cwd=project)
    if kept:
        assert r.returncode == 0, r.stderr
        assert lib in L.component_core_libs(project, d), cml.read_text(
            encoding="utf-8"
        )
    else:
        assert r.returncode == 1, r.stdout + r.stderr
        assert _tree(project) == before


def test_status_names_the_refusal_and_promises_no_apply(project: Path):
    """The issue's second half. On main: UNWIRED, and "`jm apply` writes the
    missing target_sources() line" for a library apply erased."""
    lib = _in_cmakelists(project, "o")
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 1, r.stdout
    out = r.stdout + r.stderr
    assert PROMISE not in out, out
    assert f"{lib} -> native/src/o/o_extra.cmake" in out, out


# ── a file the author keeps ─────────────────────────────────────────────────


def test_status_allow_keeps_every_component_cmakelists(project: Path):
    """gh-441 for all three peers: a named file is one apply never writes,
    so there is nothing to refuse. The module object's (`w`) alone was
    rewritten anyway."""
    rels = [f"native/src/{d}/CMakeLists.txt" for d in ("o", "m", "w")]
    _allow(project, *rels)
    for d in ("o", "m", "w"):
        _in_cmakelists(project, d)
    before = {r: (project / r).read_bytes() for r in rels}
    r = run_cli("apply", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    assert {r: (project / r).read_bytes() for r in rels} == before


def test_unwired_does_not_promise_an_apply_that_erases_it(project: Path):
    """A kept file's library is unwired, and apply writes no line for it --
    but the replay rewrites the file to classify it, so asked as "not
    unwired afterwards" the advice promised one."""
    _allow(project, "native/src/o/CMakeLists.txt")
    lib = _in_cmakelists(project, "o")
    r = run_cli("status", cwd=project)
    assert f"⊘ {lib} (native/src/o)" in r.stdout, r.stdout
    assert PROMISE not in r.stdout, r.stdout
    assert f"`jm apply` writes no line for {lib}" in r.stdout, r.stdout
    doc = json.loads(run_cli("status", "--json", cwd=project).stdout)
    (row,) = [u for u in doc["unwired_cores"] if u["core"] == lib]
    assert row["apply_wires"] is False, row


# ── the hook ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("layout", [_plain, _upper], ids=lambda f: f.__name__)
def test_moved_to_the_hook_its_wiring_survives(project: Path, layout):
    """Follow the refusal's advice. On main apply deleted the root's wiring
    as DANGLING -- a core no file declared -- and status said so."""
    lib = _in_hook(project, "o", layout)
    lines = _wire(project, lib)
    r = run_cli("apply", cwd=project)
    assert r.returncode == 0, r.stdout + r.stderr
    root = (project / "CMakeLists.txt").read_text(encoding="utf-8")
    assert all(ln in root for ln in lines), root
    s = run_cli("status", "--check", cwd=project)
    assert s.returncode == 0, s.stdout


def test_a_hook_library_nothing_wires_is_unwired(project: Path):
    """The reader sees it, so UNWIRED does -- on main `status` said OK over
    a library in no C library."""
    lib = _in_hook(project, "o")
    r = run_cli("status", "--check", cwd=project)
    assert r.returncode == 1, r.stdout
    assert f"⊘ {lib} (native/src/o)" in r.stdout, r.stdout
    assert f"`jm apply` writes no line for {lib}" in r.stdout, r.stdout


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda f: f.__name__)
def test_the_readers_survive_a_formatter(tmp_path: Path, layout):
    """Every reader this fix relies on, over each layout: the refusal's
    (`object_libraries`) and the three the hook reaches."""
    d = tmp_path / "native" / "src" / "o"
    d.mkdir(parents=True)
    (tmp_path / "CMakeLists.txt").write_text("", encoding="utf-8")
    (d / "CMakeLists.txt").write_text("", encoding="utf-8")
    text = layout(
        _block("o_helper")
        + "".join(L.wiring_line(t, "o_helper") for t in ("p_lib", "p_x"))
        + "add_library(p_x SHARED $<TARGET_OBJECTS:o_helper>)\n"
    )
    (d / "o_extra.cmake").write_text(text, encoding="utf-8")
    assert L.declared_cores(tmp_path) == {"o_helper": "o"}
    assert ("p_lib", "o_helper") in L.wired_pairs(tmp_path)
    assert L.shipped_cores(tmp_path) == {"o_helper"}
    assert L.object_libraries(text) == ["o_helper"]


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="needs cmake and a C compiler")
def test_a_hook_library_links_into_the_c_library(project: Path):
    """Text cannot show the library reached lib<pkg>; a C consumer linking
    only the static archive can."""
    lib = _in_hook(project, "o")
    _wire(project, lib)
    assert run_cli("apply", cwd=project).returncode == 0
    (project / "consumer.c").write_text(
        f"int {lib}(void);\nint main(void) {{ return {lib}() != 42; }}\n",
        encoding="utf-8",
    )
    _append(
        project / "CMakeLists.txt",
        "add_executable(consumer consumer.c)\n"
        "target_link_libraries(consumer PRIVATE p_lib_static)\n",
    )
    for cmd in (
        ["cmake", "-S", ".", "-B", "b", "-DBUILD_PYTHON=OFF"],
        ["cmake", "--build", "b", "--target", "consumer"],
    ):
        r = subprocess.run(cmd, cwd=project, capture_output=True, text=True)
        assert r.returncode == 0, (cmd, r.stdout[-3000:], r.stderr[-3000:])
