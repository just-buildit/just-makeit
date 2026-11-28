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
