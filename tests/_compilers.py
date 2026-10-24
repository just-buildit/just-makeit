"""The C and C++ compilers a test builds with: two questions, one place each.

gh-1931. The question "is there a compiler, and which" was written out in
116 places across 109 test files, as ``shutil.which("cc") or ...`` and as
``any(shutil.which(c) for c in ...)``. The copies had drifted: of the C
ones, 77 asked about ``cc``, ``gcc`` and ``clang``, 31 about ``cc`` and
``gcc``, one about ``cc`` and one about ``gcc``; of the C++ ones, three
asked about ``c++``, ``g++`` and ``clang++``, two left clang out, and one
asked for ``g++`` alone. None of the short lists said why; each was a copy
of a file that happened to stop early.

There are two questions here, and they are kept apart on purpose:

- **Which compiler does CMake use when nobody chooses?** That is what a
  generated project gets from ``cmake -S . -B build``, and what a test that
  compiles jm's output directly should use too. CMake searches an
  unversioned list, in order (``CMakeDetermineCCompiler.cmake``:
  ``cc gcc cl bcc xlc icx clang``); `C_COMPILERS` is the part of it a
  Linux or macOS box carries. :func:`default_cc` answers it, and
  :func:`default_cxx` the same for C++ (``c++ g++ ... clang++``).
- **Where is THIS compiler, whatever its version suffix?** That is
  :func:`find_compiler` (gh-1861): a Debian box ships clang only as
  ``clang-18``, which CMake's default search never finds, so a test that
  must run under clang asks for it by name.

Folding one into the other would make the first answer a box CMake cannot
build on: one whose only compiler is ``clang-18`` would run every test
that configures a generated project, and CMake, which does not search
versioned names, would then fail each of them on the box rather than on
jm.

`tests/test_gh1931_shared_test_helpers.py` refuses a test that looks a
compiler up by name with ``shutil.which`` -- directly or through a local
wrapper (gh-1976) -- instead of asking here.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys

#: CMake's unversioned default C compiler search, in its order, as far as a
#: Linux or macOS box carries the names. ``cc`` is first, so on every box
#: that has one this is the compiler CMake builds a generated project with.
C_COMPILERS = ("cc", "gcc", "clang")

#: The same for C++ (``CMakeDetermineCXXCompiler.cmake``).
CXX_COMPILERS = ("c++", "g++", "clang++")


def _first_on_path(names: "tuple[str, ...]") -> "str | None":
    """The path of the first of *names* on PATH, in order, else None."""
    for name in names:
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def default_cc() -> "str | None":
    """The C compiler CMake picks when nobody chooses one, or None.

    The first of `C_COMPILERS` on PATH, unversioned: the question is what a
    generated project builds with by default, so ``clang-18`` alone does not
    count (see :func:`find_compiler` for that question). None means no C
    compiler at all, and the caller's skip says so.

    Examples
    --------
    >>> default_cc() is None or os.path.isabs(default_cc())
    True
    """
    return _first_on_path(C_COMPILERS)


def default_cxx() -> "str | None":
    """The C++ compiler CMake picks when nobody chooses one, or None.

    The first of `CXX_COMPILERS` on PATH, unversioned, as
    :func:`default_cc` is for C.

    Examples
    --------
    >>> default_cxx() is None or os.path.isabs(default_cxx())
    True
    """
    return _first_on_path(CXX_COMPILERS)


def find_compiler(name: str) -> "str | None":
    """The path of compiler *name* on PATH, bare or versioned, else None.

    ``shutil.which(name)`` first; failing that, the newest ``<name>-<N>``
    on PATH (gh-1861). Debian installs clang only as ``clang-18`` (its
    ``clang`` is a separate package), so a bare lookup reported clang
    missing on a box that has it, and the sweep's clang leg skipped there
    -- which the skip gate rightly turns into a red ``make test``. Among
    several versions the highest number wins, and among one version's
    copies the first on PATH, which is the one a shell would run.

    Only an absent compiler is None: then the caller's skip names what is
    missing, and the skip gate holds it red, because installing a compiler
    is a fix a maintainer can make.

    `test_find_compiler_takes_the_newest_versioned_name` (in
    `test_preset_build.py`) holds each of these on a PATH it builds.

    Examples
    --------
    >>> find_compiler("no-such-cc") is None
    True
    """
    exe = shutil.which(name)
    if exe:
        return exe
    versioned = re.compile(re.escape(name) + r"-(\d+)")
    best: "tuple[int, str] | None" = None
    for d in os.environ.get("PATH", "").split(os.pathsep):
        try:
            entries = os.listdir(d or os.curdir)
        except OSError:
            continue
        for entry in entries:
            m = versioned.fullmatch(entry)
            hit = m and shutil.which(entry, path=d or os.curdir)
            if hit and (best is None or int(m.group(1)) > best[0]):
                best = (int(m.group(1)), hit)
    return best[1] if best else None


#: Where each platform's AddressSanitizer runtime is, and how it gets into an
#: interpreter that was not built with it: the runtime file names to ask the
#: compiler for, in order, and the loader variable that preloads one.
#:
#: macOS has no row, by measurement: Apple clang's
#: ``libclang_rt.asan_osx_dynamic.dylib`` resolves, but with
#: ``DYLD_INSERT_LIBRARIES`` set the runner's uv Python still loads it only
#: when the extension does, and ASan aborts with "Interceptors are not
#: working ... loaded too late (e.g. via dlopen)" (just-makeit#2353, run
#: 40042035671). See `ASAN_NO_PRELOAD_MACOS`.
_ASAN_RUNTIMES = {
    "linux": (
        ("libasan.so", f"libclang_rt.asan-{platform.machine()}.so"),
        "LD_PRELOAD",
    ),
}

#: The skip reason of an ASan test on macOS, which `tests/conftest.py`'s
#: skip gate (gh-1442) allow-lists by this exact text. Anywhere else a
#: missing runtime is `asan_skip_reason`'s other answer, which the gate
#: refuses: there a compiler install is the fix.
ASAN_NO_PRELOAD_MACOS = (
    "macOS: the ASan runtime cannot be preloaded into this interpreter"
)


def asan_env(cc: "str | None") -> "dict[str, str] | None":
    """The environment that loads *cc*'s AddressSanitizer runtime, or None.

    A test that builds a Python extension with ``-fsanitize=address`` runs
    it in an interpreter that was not, so the runtime has to be in the
    process before the extension loads. That is a preload: gcc ships
    ``libasan.so`` (clang ``libclang_rt.asan-<arch>.so``) and Linux preloads
    it with ``LD_PRELOAD``. The runtime asked for is *cc*'s own -- the
    compiler that instrumented the build -- because a gcc-instrumented
    object under clang's runtime, or the reverse, is not a supported
    pairing. macOS answers None: the preload was tried and measured not to
    take (see `_ASAN_RUNTIMES`).

    Three test files each carried a copy of this (gh-2346 review); one now.
    A caller skips with `asan_skip_reason` when this is None.

    None when *cc* is None, the platform has no entry, or *cc* has no
    runtime.

    Examples
    --------
    >>> env = asan_env(default_cc())
    >>> env is None or os.path.isfile(next(iter(env.values())))
    True
    >>> asan_env(None) is None
    True
    """
    if cc is None:
        return None
    key = sys.platform[:5]
    if key not in _ASAN_RUNTIMES:
        return None
    names, var = _ASAN_RUNTIMES[key]
    for name in names:
        lib = subprocess.run(
            [cc, f"-print-file-name={name}"], capture_output=True, text=True
        ).stdout.strip()
        # An unknown name echoes back bare, so only a real file counts.
        if os.path.isabs(lib) and os.path.isfile(lib):
            return {var: lib, "ASAN_OPTIONS": "detect_leaks=0"}
    return None


def asan_skip_reason() -> str:
    """Why an ASan test skips on this platform when `asan_env` is None.

    On macOS it is `ASAN_NO_PRELOAD_MACOS`, which the skip gate accepts,
    because no install makes the preload take. Elsewhere it names the
    missing tool, which the gate refuses.

    >>> asan_skip_reason() in (ASAN_NO_PRELOAD_MACOS,
    ...                        "no cmake / C compiler / AddressSanitizer runtime")
    True
    """
    if sys.platform == "darwin":
        return ASAN_NO_PRELOAD_MACOS
    return "no cmake / C compiler / AddressSanitizer runtime"
