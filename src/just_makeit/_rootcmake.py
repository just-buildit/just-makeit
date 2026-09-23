"""The root `CMakeLists.txt` fixes a project is missing (gh-1459, gh-1471).

The root `CMakeLists.txt` is :data:`just_makeit._createonly.PARTIAL`: `apply`
splices its marked blocks (components, modules, external dependencies) and
never touches anything else, because the rest is where an author's own
targets live (gh-959). So every fix jm makes to the root template *outside*
those blocks reaches a new project and never an existing one -- and `status`,
which cannot diff a partial file whole without calling the author's own work
drift, said nothing. gh-1452's libm link and gh-1368's Windows defaults were
both lost that way: an upgraded project that `status --check` called clean
did not link through `find_package`, and did not build under clang-cl at all.

The user settled the ownership question (2026-09-23): the file stays the
author's, `apply` writes nothing new into it, and `status` names each missing
fix **specifically**. Not a whole-file diff -- that is gh-959's noise -- but
one line per fix the template carries and the project's file does not, each
saying what breaks without it. `jm status --diff` prints the difference from
today's render beside it, so the text to merge from is one command away.

:data:`FIXES` is the table. Each row asks whether the author's file *does the
thing*, not whether it contains jm's text: the file is read as a sequence of
CMake command invocations (:func:`calls`) with comments removed, so a
formatter reflowing arguments across lines, re-indenting, or rewrapping a
comment changes nothing a row reads -- the formatter-is-the-adversary lesson,
learned on gh-1452's own anchor. And a row accepts the equivalent spellings a
CMake author would reach for (``CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS`` as well as
the target property, ``-D_USE_MATH_DEFINES`` as well as the bare define), so a
project that fixed a problem its own way is not told it did not.

A row that cannot apply -- the static library's name, where the project
declares no static library -- is skipped rather than reported, so a project
that removed jm's combined library is not told to fix one it does not have.

GATE: `tests/test_gh1459_root_cmake_findings.py` renders a fresh project and
requires zero findings (in cmake-format's other layouts too), then removes each
row's feature from that file and requires exactly that row to fire.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, NamedTuple, Optional


class Call(NamedTuple):
    """One CMake command invocation: lower-cased name and its argument words.

    Quotes are removed from quoted arguments, and parentheses inside an
    argument list (an ``if()`` condition's grouping) are dropped: no row
    asks about either.
    """

    name: str
    args: "tuple[str, ...]"


_BRACKET_COMMENT = re.compile(r"#\[(=*)\[.*?\]\1\]", re.S)
_COMMAND = re.compile(r"(?<![\w$<{:])([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")
_WORD = re.compile(r'"(?:[^"\\]|\\.)*"|[^\s()"]+')


def _strip_comments(text: str) -> str:
    """Drop ``#`` comments, keeping a ``#`` that sits inside a quoted string."""
    text = _BRACKET_COMMENT.sub(" ", text)
    out = []
    for line in text.splitlines():
        inq = False
        for i, c in enumerate(line):
            if c == '"' and (i == 0 or line[i - 1] != "\\"):
                inq = not inq
            elif c == "#" and not inq:
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def calls(text: str) -> "list[Call]":
    """Every command invocation in *text*, in file order.

    Comments are removed first, then each ``name(`` is matched to its closing
    parenthesis by depth (quoted strings skipped), so an invocation spread
    over any number of lines is one :class:`Call` -- which is what makes the
    rows below indifferent to how a formatter wraps it.
    """
    s = _strip_comments(text)
    found: list[Call] = []
    pos = 0
    while True:
        m = _COMMAND.search(s, pos)
        if not m:
            return found
        j, depth, inq = m.end(), 1, False
        while j < len(s) and depth:
            c = s[j]
            if c == '"' and s[j - 1] != "\\":
                inq = not inq
            elif not inq:
                depth += {"(": 1, ")": -1}.get(c, 0)
            j += 1
        words = tuple(
            w[1:-1] if w.startswith('"') else w
            for w in _WORD.findall(s[m.end() : j - 1])
        )
        found.append(Call(m.group(1).lower(), words))
        pos = j


class Root(NamedTuple):
    """The parsed root file, and the combined library targets it declares.

    ``shared``/``static`` are read back out of the file (an ``add_library``
    naming ``SHARED``/``STATIC`` whose name ends ``_lib``/``_lib_static``)
    rather than assumed from the manifest, on
    :func:`just_makeit._libwiring.lib_targets`'s reasoning: a project
    scaffolded before the combined library existed, or that removed it, has
    none, and a row about that library then has nothing to ask.
    """

    calls: "tuple[Call, ...]"
    shared: Optional[str]
    static: Optional[str]

    def words(self) -> "set[str]":
        return {w for c in self.calls for w in c.args}


def parse(text: str) -> Root:
    cs = tuple(calls(text))
    shared = static = None
    for c in cs:
        if c.name != "add_library" or len(c.args) < 2:
            continue
        if c.args[1] == "SHARED" and c.args[0].endswith("_lib"):
            shared = shared or c.args[0]
        if c.args[1] == "STATIC" and c.args[0].endswith("_lib_static"):
            static = static or c.args[0]
    return Root(cs, shared, static)


_TRUE = {"ON", "TRUE", "YES", "Y", "1"}


def _after(args: "tuple[str, ...]", key: str) -> Optional[str]:
    """The word following *key* in *args*, or None."""
    for i, w in enumerate(args[:-1]):
        if w == key:
            return args[i + 1]
    return None


def _has_libm(r: Root) -> bool:
    # The combined library links libm PUBLICly. `-lm` or a bare `m` links it
    # outright; `${JM_MATH_LIBRARY}` does only when something defines it --
    # a reference to an undeclared variable expands to nothing and links no
    # libm at all, which is gh-1305's quieter failure.
    defined = any(
        c.name in ("find_library", "set")
        and c.args[:1] == ("JM_MATH_LIBRARY",)
        for c in r.calls
    )
    targets = {r.shared, r.static}
    for c in r.calls:
        if c.name != "target_link_libraries" or not c.args:
            continue
        if c.args[0] not in targets and not c.args[0].startswith("${"):
            continue
        for w in c.args[1:]:
            if w in ("m", "-lm") or "-lm>" in w:
                return True
            if "JM_MATH_LIBRARY" in w and defined:
                return True
    return False


def _has_static_name(r: Root) -> bool:
    # A property call naming the static library and NOT the shared one, that
    # gives it an OUTPUT_NAME other than the project's own: the shared
    # library's import library is then no longer the same file.
    pkg = r.shared[: -len("_lib")] if r.shared else None
    for c in r.calls:
        if c.name not in ("set_target_properties", "set_property"):
            continue
        if r.static not in c.args or r.shared in c.args:
            continue
        name = _after(c.args, "OUTPUT_NAME")
        if name and name != pkg:
            return True
    return False


def _has_export_all(r: Root) -> bool:
    return any(
        _after(c.args, key) in _TRUE
        for c in r.calls
        for key in (
            "WINDOWS_EXPORT_ALL_SYMBOLS",
            "CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS",
        )
    )


def _has_runtime_dest(r: Root) -> bool:
    return any(
        c.name == "install"
        and "TARGETS" in c.args
        and r.shared in c.args
        and "RUNTIME" in c.args
        for c in r.calls
    )


def _has_default_build_type(r: Root) -> bool:
    # Decided BEFORE project(): that is where CMake fills in its own default,
    # which is Debug for clang-cl.
    for c in r.calls:
        if c.name == "project":
            return False
        if c.name == "set" and c.args[:1] == ("CMAKE_BUILD_TYPE",):
            return True
    return False


def _has_msvc_runtime(r: Root) -> bool:
    return any(
        (c.name == "set" and c.args[:1] == ("CMAKE_MSVC_RUNTIME_LIBRARY",))
        or (
            c.name in ("set_target_properties", "set_property")
            and "MSVC_RUNTIME_LIBRARY" in c.args
        )
        for c in r.calls
    )


def _has_complex_range(r: Root) -> bool:
    return any("-fcx-limited-range" in w for w in r.words())


_WIN_DEFINES = (
    "_CRT_SECURE_NO_WARNINGS",
    "_CRT_NONSTDC_NO_DEPRECATE",
    "_USE_MATH_DEFINES",
)


def _has_win_defines(r: Root) -> bool:
    defined = {re.sub(r"^[-/]D", "", w) for w in r.words()}
    return all(d in defined for d in _WIN_DEFINES)


class Fix(NamedTuple):
    """One fix the root template carries outside jm's managed blocks.

    ``key`` names it in the report and in ``status_allow``
    (``CMakeLists.txt:<key>``). ``platform`` is where its absence breaks the
    build, so a reader who does not target that platform can decide it is
    not theirs. ``missing`` is the report line: what goes wrong without it,
    which is what makes the finding something to act on rather than jm
    asking for its own text back. ``applies`` skips a row about a target the
    project does not declare.
    """

    key: str
    issue: str
    platform: str
    missing: str
    present: Callable[[Root], bool]
    applies: Callable[[Root], bool] = lambda r: True


FIXES: "tuple[Fix, ...]" = (
    Fix(
        "libm",
        "gh-1452",
        "all",
        "the combined library does not link libm PUBLIC, so a C consumer "
        "built through find_package() fails with undefined math symbols",
        _has_libm,
        lambda r: bool(r.shared or r.static),
    ),
    Fix(
        "static-name",
        "gh-1368",
        "Windows",
        "the static library shares the shared library's OUTPUT_NAME, so "
        "both produce <name>.lib: `ninja: error: multiple rules generate`",
        _has_static_name,
        lambda r: bool(r.shared and r.static),
    ),
    Fix(
        "export-all",
        "gh-1368",
        "Windows",
        "the shared library does not set WINDOWS_EXPORT_ALL_SYMBOLS, so its "
        "DLL exports nothing and every C consumer fails to link",
        _has_export_all,
        lambda r: bool(r.shared),
    ),
    Fix(
        "runtime-dest",
        "gh-1368",
        "Windows",
        "install(TARGETS) has no RUNTIME DESTINATION, so the DLL is never "
        "installed beside its import library",
        _has_runtime_dest,
        lambda r: bool(r.shared),
    ),
    Fix(
        "build-type",
        "gh-1368",
        "Windows (clang-cl)",
        "no default CMAKE_BUILD_TYPE before project(), so a bare configure "
        "is Debug and links the debug interpreter's python3X_d.lib",
        _has_default_build_type,
    ),
    Fix(
        "msvc-runtime",
        "gh-1368",
        "Windows (clang-cl)",
        "CMAKE_MSVC_RUNTIME_LIBRARY is not pinned to the release CRT, so a "
        "Debug build links python3X_d.lib, which only a debug Python ships",
        _has_msvc_runtime,
    ),
    Fix(
        "complex-range",
        "gh-1368",
        "Windows (clang-cl)",
        "no /clang:-fcx-limited-range, so complex multiply and divide "
        "link against __mulsc3/__divsc3, which the MSVC link lacks",
        _has_complex_range,
    ),
    Fix(
        "win-defines",
        "gh-1368",
        "Windows",
        "the CRT and math defines are missing ("
        + ", ".join(_WIN_DEFINES)
        + "): "
        "M_PI is undefined and every portable C99 call warns",
        _has_win_defines,
    ),
)


def missing(root: Path) -> "list[Fix]":
    """The :data:`FIXES` rows the project's root ``CMakeLists.txt`` lacks.

    Empty when there is no root ``CMakeLists.txt`` (the ``make`` build
    backend has none), and never includes a row whose ``applies`` is false.
    """
    path = root / "CMakeLists.txt"
    if not path.is_file():
        return []
    r = parse(path.read_text(encoding="utf-8", errors="replace"))
    return [f for f in FIXES if f.applies(r) and not f.present(r)]
