"""Compile C and C++ in a test: one helper, in either compiler dialect.

gh-2294. A test that builds jm's output without CMake -- a Python
extension from a generated ``_ext.c``, an object to prove a header
compiles, an executable to run -- wrote its compiler command by hand, in
the ``cc`` dialect: ``-shared`` (``-bundle`` on macOS), ``-fPIC``,
``-std=c11``, ``-I``, ``-o``. Some fifty sites in as many files, and
clang-cl, the compiler a Windows project builds with (gh-1368), takes
none of the structural ones. So no compiled unit test ran on Windows.

:func:`build` is the one place a test turns "these sources, this kind of
output" into a command line. It speaks two dialects, chosen by the
compiler it runs:

- **cc** (gcc, clang, Apple clang): the spelling the sites used.
- **msvc** (clang-cl): ``/c``, ``/LD``, ``/Fo``, ``/Fe``, ``/I``, ``/D``,
  and a ``/link /LIBPATH`` to the interpreter's import library. A
  language standard or a warning flag is a clang driver option and has
  no cl spelling worth a table (``-std=gnu99`` has none at all), so it
  goes through ``/clang:``, which clang-cl hands to the driver verbatim.
  And a bare ``-Wall`` would be cl's ``/Wall``, which is clang's
  ``-Weverything`` -- the wrong flag, read silently.

What a caller says is the INTENT -- the kind of output, the standard,
the warnings -- and never a structural flag, so a fixture written on
Linux is a fixture that builds on Windows. ``extra`` takes arguments
verbatim, for a flag set some other tool printed (``pkg-config
--cflags``); a caller who passes it owns the dialect.

The compiler is :func:`_compilers.default_cc` (``default_cxx`` for C++),
which honours ``CC`` / ``CXX`` as CMake does, so the Windows CI leg's
``CC=clang-cl`` reaches every fixture with no per-test switch.

`tests/test_gh2294_one_compile_helper.py` refuses a test that runs a
compiler, or spells a structural flag, anywhere but here.

Examples
--------
>>> cmd = command("ext", ["m.c"], "out/m.so", compiler="cc",
...               std="c11", python=False)
>>> cmd[:2] == ["cc", "-shared"] or cmd[:2] == ["cc", "-bundle"]
True
>>> command("obj", ["m.c"], "m.obj", compiler="clang-cl", std="c11",
...         warnings=["all", "error"])[:6]
['clang-cl', '/nologo', '/c', '/clang:-std=c11', '/clang:-Wall', \
'/clang:-Werror']
"""

from __future__ import annotations

import os
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from _compilers import default_cc, default_cxx

#: The kinds of build :func:`build` runs: a syntax check, an object file,
#: an executable, and a CPython extension module.
KINDS = ("syntax", "obj", "exe", "ext")

#: The compiler basenames that speak the msvc dialect.
MSVC_NAMES = ("clang-cl", "cl")

#: Libraries a POSIX link names and the MSVC CRT already carries.
_CRT_LIBS = frozenset({"m", "c", "pthread", "dl"})


def is_msvc(compiler: "str | os.PathLike[str]") -> bool:
    """Whether *compiler* takes cl's options rather than cc's.

    By basename, without a version suffix or ``.exe``: ``clang-cl.exe``
    and ``/usr/bin/clang-cl-18`` both do.

    Examples
    --------
    >>> is_msvc("C:/LLVM/bin/clang-cl.exe"), is_msvc("/usr/bin/gcc")
    (True, False)
    """
    stem = Path(compiler).name.lower()
    if stem.endswith(".exe"):
        stem = stem[: -len(".exe")]
    base = stem.rstrip("0123456789").rstrip("-")
    return base in MSVC_NAMES


def compiler_for(cxx: bool = False) -> "str | None":
    """The compiler a fixture builds with, or None when there is none.

    :func:`_compilers.default_cxx` for C++, else ``default_cc``. A C++
    fixture under clang-cl uses clang-cl itself: it compiles ``.cpp`` as
    C++ by extension, and the C++ lookup has no ``clang-cl`` to find.

    Examples
    --------
    >>> compiler_for() is None or os.path.isabs(compiler_for())
    True
    """
    cc = default_cc()
    if cxx and not (cc and is_msvc(cc)):
        return default_cxx()
    return cc


def suffix(kind: str, compiler_path: "str | None" = None) -> str:
    """The file suffix of a *kind* output on this platform.

    An extension takes the interpreter's ``EXT_SUFFIX``; an object is
    ``.obj`` in the msvc dialect and ``.o`` otherwise; an executable is
    ``.exe`` on Windows and bare elsewhere.

    Examples
    --------
    >>> suffix("obj", "clang-cl"), suffix("obj", "cc")
    ('.obj', '.o')
    """
    if kind == "ext":
        return sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    if kind == "obj":
        cc = compiler_path or compiler_for() or "cc"
        return ".obj" if is_msvc(cc) else ".o"
    if kind == "exe":
        return ".exe" if sys.platform == "win32" else ""
    raise ValueError(f"unknown output kind {kind!r}; one of {KINDS}")


def output(
    directory: "str | os.PathLike[str]",
    stem: str,
    kind: str,
    cxx: bool = False,
) -> Path:
    """The path a *kind* output named *stem* gets in *directory*.

    Examples
    --------
    >>> output("b", "m", "ext").name.startswith("m.")
    True
    """
    return Path(directory) / (stem + suffix(kind, compiler_for(cxx)))


def _python_libdir() -> str:
    """The directory holding the interpreter's import library (Windows).

    A Windows extension links ``pythonXY.lib``, which ``pyconfig.h``
    names with a ``#pragma comment(lib)``; the linker finds it under
    the base installation's ``libs``, whatever venv runs the test.
    """
    return os.path.join(sys.base_exec_prefix, "libs")


#: The oldest C++ the MSVC standard library compiles as: it has no C++11
#: mode (its headers deduce return types), and cl has no ``/std:c++11``.
_MSVC_CXX_FLOOR = 14


def _std(std: str) -> str:
    """*std*, raised to the oldest C++ the platform's library supports.

    On Windows a C++ translation unit includes the MSVC STL, which needs
    C++14, so ``c++11`` means the oldest C++ there is: ``c++14``. A C
    standard, and every standard elsewhere, is unchanged.

    Examples
    --------
    >>> _std("c11")
    'c11'
    >>> _std("c++11") == ("c++14" if sys.platform == "win32" else "c++11")
    True
    """
    for lang in ("c++", "gnu++"):
        ver = std[len(lang) :] if std.startswith(lang) else ""
        if sys.platform == "win32" and ver.isdigit():
            if int(ver) < _MSVC_CXX_FLOOR:
                return f"{lang}{_MSVC_CXX_FLOOR}"
    return std


def command(
    kind: str,
    sources: "Iterable[str | os.PathLike[str]]",
    out: "str | os.PathLike[str] | None" = None,
    *,
    compiler: "str | os.PathLike[str]",
    std: "str | None" = None,
    warnings: "Sequence[str]" = (),
    flags: "Sequence[str]" = (),
    includes: "Sequence[str | os.PathLike[str]]" = (),
    defines: "Sequence[str]" = (),
    libs: "Sequence[str]" = (),
    python: "bool | None" = None,
    numpy: bool = False,
    extra: "Sequence[str]" = (),
) -> "list[str]":
    """The command line that builds *sources* into *out*.

    Parameters
    ----------
    kind : str
        One of `KINDS`: ``"syntax"`` (check only, no output),
        ``"obj"`` (compile only), ``"exe"``, or ``"ext"`` (a CPython
        extension module).
    sources : iterable of path
        C or C++ sources, or objects and archives to link.
    out : path, optional
        The output file, suffix included (see :func:`output`). None, and
        only None, for ``"syntax"``.
    compiler : path
        The compiler to run; its basename picks the dialect.
    std : str, optional
        A language standard as the clang driver spells it, without
        ``-std=``: ``"c11"``, ``"gnu99"``, ``"c++11"``.
    warnings : sequence of str
        Warning options without ``-W``: ``"all"``, ``"error"``,
        ``"error=implicit-function-declaration"``.
    flags : sequence of str
        Any other driver option, in the clang driver's spelling
        (``"-O2"``, ``"-g"``). In the msvc dialect each goes through
        ``/clang:``, so it means what it means under cc.
    includes : sequence of path
        Include directories.
    defines : sequence of str
        Macro definitions, ``"NAME"`` or ``"NAME=VALUE"``.
    libs : sequence of str
        Libraries to link, by name (``"m"``). In the msvc dialect the
        ones the CRT already carries are dropped.
    python : bool, optional
        Add the interpreter's include directory. Defaults to True for
        an extension and False otherwise.
    numpy : bool
        Add numpy's include directory.
    extra : sequence of str
        Arguments appended verbatim. The caller owns their dialect.

    Returns
    -------
    list of str
        The argv, compiler first.

    Examples
    --------
    >>> command("exe", ["a.o", "b.o"], "prog", compiler="cc",
    ...         libs=["m"])
    ['cc', 'a.o', 'b.o', '-o', 'prog', '-lm']
    >>> command("exe", ["a.obj"], "prog.exe", compiler="clang-cl",
    ...         libs=["m"], flags=["-O2"])
    ['clang-cl', '/nologo', '/clang:-O2', 'a.obj', '/Feprog.exe']
    >>> command("syntax", ["a.c"], compiler="cc", std="gnu99")
    ['cc', '-fsyntax-only', '-std=gnu99', 'a.c']
    """
    if kind not in KINDS:
        raise ValueError(f"unknown output kind {kind!r}; one of {KINDS}")
    if (out is None) != (kind == "syntax"):
        raise ValueError(f"a {kind!r} build takes an output iff not syntax")
    if python is None:
        python = kind == "ext"
    incs = [str(i) for i in includes]
    if python:
        incs.append(sysconfig.get_path("include"))
    if numpy:
        import numpy as np

        incs.append(np.get_include())
    srcs = [str(s) for s in sources]
    driver = [f"-std={_std(std)}"] if std else []
    driver += [f"-W{w}" for w in warnings]
    driver += list(flags)
    if is_msvc(compiler):
        argv = [str(compiler), "/nologo"]
        argv += {"syntax": ["/Zs"], "obj": ["/c"], "ext": ["/LD"]}.get(
            kind, []
        )
        argv += [f"/clang:{f}" for f in driver]
        argv += [f"/I{i}" for i in incs]
        argv += [f"/D{d}" for d in defines]
        argv += srcs
        if out is not None:
            argv.append(("/Fo" if kind == "obj" else "/Fe") + str(out))
        argv += [f"{lib}.lib" for lib in libs if lib not in _CRT_LIBS]
        argv += list(extra)
        if kind == "ext":
            argv += ["/link", f"/LIBPATH:{_python_libdir()}"]
        return argv
    argv = [str(compiler)]
    if kind == "ext":
        if sys.platform == "darwin":
            # macOS keeps CPython's symbols unresolved in the bundle, to be
            # bound by the interpreter that loads it.
            argv += ["-bundle", "-undefined", "dynamic_lookup"]
        else:
            argv.append("-shared")
    if kind in ("ext", "obj") and sys.platform != "win32":
        # Position-independent code is a question for ELF and Mach-O; a
        # clang that targets the MSVC ABI refuses the flag outright.
        argv.append("-fPIC")
    argv += {"syntax": ["-fsyntax-only"], "obj": ["-c"]}.get(kind, [])
    argv += driver
    argv += [f"-I{i}" for i in incs]
    argv += [f"-D{d}" for d in defines]
    argv += srcs
    if out is not None:
        argv += ["-o", str(out)]
    argv += [f"-l{lib}" for lib in libs]
    argv += list(extra)
    return argv


@dataclass
class Built:
    """What :func:`build` made: the output path and the compiler's run."""

    path: "Path | None"
    args: "list[str]"
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the compiler exited 0."""
        return self.returncode == 0


def build(
    kind: str,
    sources: "Iterable[str | os.PathLike[str]]",
    out: "str | os.PathLike[str] | None" = None,
    *,
    cxx: bool = False,
    compiler: "str | os.PathLike[str] | None" = None,
    check: bool = True,
    cwd: "str | os.PathLike[str] | None" = None,
    env: "dict[str, str] | None" = None,
    timeout: float = 600,
    **options,
) -> Built:
    """Build *sources* into *out* and say what happened.

    *out* is used as given; :func:`output` names it portably. *options*
    are :func:`command`'s keywords. The compiler is *compiler*, else
    :func:`compiler_for` the language.

    With *check* (the default) a failing build is an ``AssertionError``
    carrying the command and the compiler's output, so a fixture reads
    ``build(...).path``; a test about a build that must FAIL passes
    ``check=False`` and reads ``returncode`` and ``stderr``.

    Examples
    --------
    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as d:
    ...     src = Path(d) / "m.c"
    ...     _ = src.write_text("int f(void) { return 1; }\\n")
    ...     b = build("obj", [src], output(d, "m", "obj"))
    ...     b.ok and b.path.is_file()
    True
    """
    cc = compiler if compiler is not None else compiler_for(cxx)
    if cc is None:
        raise RuntimeError("no C compiler on PATH")
    argv = command(kind, sources, out, compiler=cc, **options)
    r = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=timeout,
    )
    path = None if out is None else Path(out)
    built = Built(path, argv, r.returncode, r.stdout, r.stderr)
    if check:
        assert built.ok, (
            f"build failed ({r.returncode}): {' '.join(argv)}\n"
            f"{r.stdout}{r.stderr}"
        )
    return built
