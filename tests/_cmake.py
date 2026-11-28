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


def unnamed_configure(args: object) -> "list[str] | None":
    """The argv, if *args* -- what a test hands ``subprocess`` -- is a cmake
    configure that builds the extension for no interpreter it names.

    The runtime side of gh-2857's guard, which `tests/conftest.py` asks of
    every process a test starts. A static scan of the test files can only
    learn spellings one at a time -- a variable holding ``which("cmake")``,
    a helper's ``*args``, a positional source -- and each it missed was a
    test building for the first ``python3`` on ``PATH``. The argv as it
    runs has no spelling left.

    *args* is a sequence or a string (``shell=True``). A configure passes
    when it names an interpreter (``-DPython3_EXECUTABLE=...``, this one or
    another on purpose) or builds no extension (``-DBUILD_PYTHON=OFF``).

    >>> unnamed_configure(["/usr/bin/cmake", "-S", ".", "-B", "b"])
    ['/usr/bin/cmake', '-S', '.', '-B', 'b']
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
    return [str(a) for a in argv]
