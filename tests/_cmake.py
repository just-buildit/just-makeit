"""The interpreter a test's own ``cmake`` configure builds the extension for.

gh-2857, the test-suite side of gh-814. gh-814 made every cmake call the
GENERATED project makes name its interpreter, because ``FindPython3`` left
to itself takes the first one it finds. Tests that configure a scaffolded
project with ``cmake`` directly did not: they built the extension for
whatever ``python3`` was first on ``PATH`` -- ``/usr/bin/python3`` 3.14 on a
box whose suite runs 3.12 -- and the import then failed (``No module named
'p.dsp.filters.filters'``). CI passed only because its image carries one
interpreter.

:data:`PYTHON3_ARG` names ``sys.executable``: the parent interpreter, whose
numpy ``FindPython3`` finds directly. Not the per-worker venv, whose numpy
is reachable only through a ``.pth`` that the NumPy probe does not honour
(``tests/conftest.py``, gh-879).

Every ``["cmake", "-S", ...]`` configure in ``tests/`` passes it, or passes
``-DBUILD_PYTHON=OFF`` and builds no extension at all.
``tests/test_gh2857_cmake_names_python.py`` refuses one that does neither.
"""

from __future__ import annotations

import sys

#: The one spelling of "build the extension for the interpreter running
#: this test".
PYTHON3_ARG = f"-DPython3_EXECUTABLE={sys.executable}"


#: A cmake command with one of these is not a configure (gh-2857). Every
#: other one is: a source given positionally, as an attached or f-string
#: flag, through a helper's ``*args`` or a variable -- whatever the
#: spelling, it is checked as it runs (:func:`unnamed_configure`).
NOT_CONFIGURE = (
    "--build",
    "--install",
    "-E",
    "-P",
    "--preset",
    "--version",
    "--help",
)


_PYTHON_CMAKE = ("find_package(python3", "python3_add_library")


def _source_dir(rest: "list[str]", cwd: "object") -> "str":
    """The source directory a configure reads: ``-S dir``, ``-Sdir``, or a
    positional argument, else the working directory."""
    import os

    it = iter(range(len(rest)))
    for i in it:
        a = rest[i]
        if a == "-S" and i + 1 < len(rest):
            return rest[i + 1]
        if a.startswith("-S") and len(a) > 2:
            return a[2:]
        if a in ("-B", "-G", "-T", "-A", "-C", "-D", "-U") and i + 1 < len(
            rest
        ):
            next(it, None)  # that flag's value, not a positional
            continue
        if not a.startswith("-"):
            return a
    return os.fspath(cwd) if cwd is not None else os.getcwd()


def builds_python(source: "object") -> bool:
    """Whether the CMake project at *source* builds a Python extension: any
    ``CMakeLists.txt`` or ``.cmake`` file in it (a build tree's excepted)
    asks for ``find_package(Python3 ...)`` or ``Python3_add_library``.

    A C-only consumer -- ``project(smoke C)`` linking an installed library
    -- has no interpreter to name, and naming one would claim it builds
    Python (gh-2857). Read from the tree, not from a list of exemptions.
    """
    from pathlib import Path

    root = Path(source)
    if not root.is_dir():
        return True  # cannot tell: keep the guard
    for f in root.rglob("*"):
        if f.name != "CMakeLists.txt" and f.suffix != ".cmake":
            continue
        if any((p / "CMakeCache.txt").exists() for p in f.parents):
            continue  # a build tree's generated files
        try:
            text = f.read_text(encoding="utf-8", errors="replace").lower()
        except OSError:
            continue
        if any(k in text for k in _PYTHON_CMAKE):
            return True
    return False


def unnamed_configure(args: object, cwd: object = None) -> "list[str] | None":
    """The argv, if *args* -- what a test hands ``subprocess`` -- is a cmake
    configure that builds the extension for no interpreter it names.

    The runtime side of gh-2857's guard, which `tests/conftest.py` asks of
    every process a test starts. A static scan of the test files can only
    learn spellings one at a time -- a variable holding ``which("cmake")``,
    a helper's ``*args``, a positional source -- and each it missed was a
    test building for the first ``python3`` on ``PATH``. The argv as it
    runs has no spelling left.

    *args* is a sequence or a string (``shell=True``); *cwd* is the
    process's working directory. A configure passes when it names an
    interpreter (``-DPython3_EXECUTABLE=...``, this one or another on
    purpose), builds no extension (``-DBUILD_PYTHON=OFF``), or configures a
    project whose CMake builds no Python at all (:func:`builds_python`).

    >>> unnamed_configure(["/usr/bin/cmake", "-S", "/no/such/src", "-B", "b"])
    ['/usr/bin/cmake', '-S', '/no/such/src', '-B', 'b']
    >>> unnamed_configure(["cmake", "..", PYTHON3_ARG]) is None
    True
    >>> unnamed_configure(["cmake", "--build", "b"]) is None
    True
    >>> unnamed_configure(["cmake", "-B", "b", "-DBUILD_PYTHON=OFF"]) is None
    True
    >>> unnamed_configure(["make", "-C", "b"]) is None
    True
    """
    import os
    import shlex

    if isinstance(args, (str, bytes)):
        text = args.decode() if isinstance(args, bytes) else args
        argv = shlex.split(text)
    else:
        try:
            argv = [os.fspath(a) for a in args]  # type: ignore[union-attr]
        except TypeError:
            return None
    if not argv:
        return None
    prog = os.path.basename(str(argv[0])).lower()
    if prog not in ("cmake", "cmake.exe"):
        return None
    rest = [str(a) for a in argv[1:]]
    if any(
        a == n or a.startswith(n + "=") for a in rest for n in NOT_CONFIGURE
    ):
        return None
    if any(
        a.startswith("-DPython3_EXECUTABLE=") or a == "-DBUILD_PYTHON=OFF"
        for a in rest
    ):
        return None
    import os

    base = os.fspath(cwd) if cwd is not None else os.getcwd()
    # A relative source is the PROCESS's, not this one's.
    if not builds_python(os.path.join(base, _source_dir(rest, cwd))):
        return None
    return [str(a) for a in argv]
