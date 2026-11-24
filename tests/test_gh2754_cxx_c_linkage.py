"""gh-2754: every function a generated header declares has C linkage in C++.

`<comp>_procglobal.h` (gh-1117) declared its two accessors at file scope
with no ``extern "C"`` block. It parsed as C++ -- gh-1148's sweep compiles
every generated header as C++11 and was green -- so nothing noticed that a
C++ includer gave both functions C++ linkage: a mangled symbol the C core
never defines, and a failed link. doppler found it (doppler#3291) with a
check that reads linkage off clang++'s AST.

Parsing is not linkage, so this asks the second question directly, of the
compiler, for every header jm writes:

1. **What does the header declare?** GNU gcc's ``-aux-info`` lists every
   function a C translation unit declares, with its storage class and the
   file and line of the declaration. That is the compiler's reading, after
   the preprocessor: a prototype quoted in a comment -- and generated
   headers carry prose about themselves -- is not in it, and a macro that
   expands to a declaration is. Each non-``static`` one whose declaration
   is in the header under test is that header's to guard; a ``static`` or
   ``static inline`` function has internal linkage and nothing to link.
2. **Does C++ see C linkage?** A C++ translation unit includes the header,
   then redeclares each of those functions ``extern "C"``, through
   ``decltype`` so no signature is respelled. Redeclaring a function with
   a different language linkage is ill-formed ([dcl.link]); g++ and
   clang++ both refuse it, naming the function and its previous
   declaration. A function already inside ``extern "C"`` redeclares
   cleanly.

Registration-free over headers: they are every ``.h`` under each project's
``native/`` in `tests/_csym_fixtures.py`'s tree -- the broad tree built by
running jm, which holds a process-global component, a composer with
straight-C seams, a handle module, perf, functions, views and a dotted
module. A new header kind is covered the day a fixture row generates it,
and none is listed here. `test_the_sweep_is_armed` asserts the walk found
the kinds this issue is about, so a sweep over nothing cannot pass.

GATE: no header jm generates declares a function with C++ linkage.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

import _cbuild as cbuild
import _csym_fixtures as FX
import pytest
from _compilers import WINDOWS_NO_GNU_CC, find_gnu_cc
from _jminc import INC_DIR, INC_ROOT

_GNU_CC = find_gnu_cc()
_CXX = cbuild.compiler_for(cxx=True)

if _GNU_CC is None or _CXX is None:
    pytestmark = pytest.mark.skip(
        reason=WINDOWS_NO_GNU_CC
        if sys.platform == "win32"
        else "needs GNU gcc (-aux-info) and a C++ compiler"
    )

#: One ``-aux-info`` line: ``/* <file>:<line>:<flags> */ <declaration>;``.
_AUX = re.compile(r"^/\* (?P<file>.+):(?P<line>\d+):\w+ \*/ (?P<decl>.*)$")

#: The declared name: the identifier its parameter list follows.
_NAME = re.compile(r"(\w+) \(")

#: The author's half of the tree, which jm leaves to the project and the
#: fixture rows never write: the `playlist` composer's backing header,
#: holding its source struct `clip_t`. A header that does not compile has
#: no declarations to check, so every header must compile, and this is
#: what lets the composer's ``_bridge.h`` do so.
_AUTHOR_HEADERS = {
    ("kd", "playlist/playlist_core.h"): (
        "#ifndef PLAYLIST_CORE_H\n#define PLAYLIST_CORE_H\n"
        "typedef struct { double gain; } clip_t;\n#endif\n"
    ),
}

#: The other author-declared names: the record types two `ser` methods
#: return (``--return-type peak_t`` / ``best_t``). Forced in front of
#: every probe with ``-include``; an incomplete type is enough for a
#: prototype.
_AUTHOR_PRELUDE = (
    "typedef struct peak_t peak_t;\ntypedef struct best_t best_t;\n"
)


class Header(NamedTuple):
    """One generated header, its project, and what it declares."""

    root: Path
    path: Path
    functions: "list[str]"


def _author_half(roots: "dict[str, Path]", work: Path) -> Path:
    """Write `_AUTHOR_HEADERS` into *roots*; return the prelude's path."""
    for (pkg, rel), text in _AUTHOR_HEADERS.items():
        root = next(r for r in roots.values() if r.name == pkg)
        path = root / INC_ROOT / rel
        assert not path.exists(), f"jm now writes {path}; drop it here"
        path.write_text(text, encoding="utf-8")
    prelude = work / "author_prelude.h"
    prelude.write_text(_AUTHOR_PRELUDE, encoding="utf-8")
    return prelude


def external_functions(
    root: Path, header: Path, prelude: Path, tmp: Path
) -> "list[str]":
    """The functions *header* itself declares with external linkage.

    Asked of GNU gcc (``-aux-info``) over a C translation unit that
    includes *header*. Only declarations whose file is *header* count, so
    a function from a header it includes is attributed to that header.
    """
    probe = tmp / "aux_probe.c"
    probe.write_text(f'#include "{header.as_posix()}"\n', encoding="utf-8")
    aux = tmp / "aux.txt"
    r = cbuild.build(
        "syntax",
        [probe],
        compiler=_GNU_CC,
        check=False,
        flags=["-include", str(prelude)],
        includes=[root / INC_DIR],
        python=True,
        extra=["-aux-info", str(aux)],
    )
    assert r.ok, f"{header}: does not compile as C\n{r.stderr}"
    names = []
    target = header.resolve()
    for line in aux.read_text(encoding="utf-8").splitlines():
        m = _AUX.match(line)
        if not m or Path(m["file"]).resolve() != target:
            continue
        if m["decl"].startswith("static "):
            continue
        name = _NAME.search(m["decl"])
        assert name, f"{header}: unparsed -aux-info line\n{line}"
        names.append(name.group(1))
    return names


def cxx_linkage_errors(h: Header, prelude: Path, tmp: Path) -> "str | None":
    """The C++ compiler's refusal when a function of *h* lacks C linkage.

    None when every one of them redeclares ``extern "C"`` cleanly.
    """
    probe = tmp / "linkage_probe.cpp"
    probe.write_text(
        f'#include "{h.path.as_posix()}"\n'
        + "".join(f'extern "C" decltype({n}) {n};\n' for n in h.functions),
        encoding="utf-8",
    )
    r = cbuild.build(
        "syntax",
        [probe],
        cxx=True,
        check=False,
        std="c++11",
        flags=["-include", str(prelude)],
        includes=[h.root / INC_DIR],
        python=True,
    )
    return None if r.ok else r.stderr.strip()


class Tree(NamedTuple):
    """Every generated header in the fixture tree, and the prelude."""

    headers: "list[Header]"
    prelude: Path


@pytest.fixture(scope="module")
def tree(tmp_path_factory) -> Tree:
    roots = FX.build(tmp_path_factory.mktemp("cxx_linkage"))
    work = tmp_path_factory.mktemp("probe")
    prelude = _author_half(roots, work)
    headers = [
        Header(root, h, external_functions(root, h, prelude, work))
        for root in roots.values()
        for h in sorted((root / "native").rglob("*.h"))
    ]
    return Tree(headers, prelude)


def test_the_sweep_is_armed(tree: Tree) -> None:
    """The walk reached the header kinds this issue is about, each with
    the functions it declares -- so a sweep over nothing cannot pass."""
    found = {h.path.name: h.functions for h in tree.headers}
    for suffix, fn in (
        ("_procglobal.h", "_state_ptr"),
        ("_bridge.h", "_from_source"),
        ("_core.h", "_create"),
    ):
        assert any(
            n.endswith(fn)
            for name, names in found.items()
            if name.endswith(suffix)
            for n in names
        ), (suffix, fn, found)


def test_every_declared_function_has_c_linkage(
    tree: Tree, tmp_path: Path
) -> None:
    failures = []
    for h in tree.headers:
        if not h.functions:
            continue
        err = cxx_linkage_errors(h, tree.prelude, tmp_path)
        if err is not None:
            rel = h.path.relative_to(h.root.parent).as_posix()
            failures.append(f"--- {rel} ({', '.join(h.functions)})\n{err}")
    assert not failures, "\n".join(failures)
