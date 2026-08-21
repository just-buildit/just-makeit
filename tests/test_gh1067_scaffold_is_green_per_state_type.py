"""gh-1067: a scaffold must be GREEN on its first run, for every state type.

`jm new --state "flag:bool:false"` produced a project whose generated CTest
*and* generated pytest both failed immediately, with no edits: the smoke
round-trip emitted `set(2)` then asserted `== 2`, which a C `bool` can never
satisfy because it normalises every non-zero to 1.

The cause was gh-610's shape at a third and fourth site. That issue
established that `bool`'s `kind` is `"int"` -- there is no distinct "bool"
kind -- so `bool` silently takes the integer path unless the concrete ctype is
special-cased, and it added that case to `_py_default` and
`_py_default_stub`. The two sample-value helpers were the peers it did not
reach, and they are the ones the generated TESTS read.

The gate BUILDS AND RUNS the scaffold, both faces, for every scalar type the
type vocabulary admits. Two weaker versions were written first and both were
thrown away after sabotage proved them useless, which is worth recording:

* A textual check that the round-trip "assigns what it expects" passes on the
  broken code -- it emitted `set(obj, 2)` and `== 2`, which are textually
  identical. The defect is semantic, in C's conversion, and the only way to
  see it without modelling C is to compile and run it.
* Running the C face alone leaves the Python face uncovered; reverting only
  the `_py_sample_val` half kept every assertion green.

The parametrisation is over the vocabulary rather than over `bool`, so a type
added to `_CTYPE_META` is covered with no edit here -- and because a check
written for `bool` alone would not have caught this before `bool` existed, nor
catch whatever the next such type is.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from just_makeit._context._types import (  # noqa: E402
    _c_set_val,
    _py_sample_val,
)
from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._types import _CTYPE_META  # noqa: E402

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)

#: Every scalar type a `--state` field may carry, derived from the type
#: vocabulary rather than listed, so a new one is covered without an edit.
SCALAR_TYPES = sorted(
    ct
    for ct, meta in _CTYPE_META.items()
    if not ct.endswith("[]")
    and meta.get("kind") in ("int", "float", "complex")
)


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def test_the_vocabulary_is_not_empty():
    """Never vacuous: an empty parametrisation would report all-green."""
    assert len(SCALAR_TYPES) >= 10, SCALAR_TYPES
    assert "bool" in SCALAR_TYPES


def test_a_bool_set_value_is_a_bool_on_both_faces():
    """The specific regression, asserted once and directly."""
    assert _c_set_val("bool") == "true"
    assert _py_sample_val(_CTYPE_META["bool"], "bool") == "True"


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
@pytest.mark.parametrize("ctype", SCALAR_TYPES)
def test_an_untouched_scaffold_passes_its_own_tests(ctype, tmp_path):
    """Scaffold one object carrying *ctype*, build it, run BOTH suites.

    No implementation step and no hand edits: whatever `jm new` writes has to
    pass on its own. That is the "all green from day one" contract, and it is
    the only formulation that catches a value which is wrong after the C
    compiler has looked at it.
    """
    root = tmp_path / "demo"
    _quiet(
        new_run,
        "demo",
        root,
        object_names=["thing"],
        state_vars=[("flag", ctype, _CTYPE_META[ctype]["zero"])],
        arg_type="double",
        return_type="double",
    )
    cfg = subprocess.run(
        [
            "cmake",
            "-S",
            str(root),
            "-B",
            str(root / "build"),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        capture_output=True,
        text=True,
    )
    assert cfg.returncode == 0, cfg.stdout + cfg.stderr
    built = subprocess.run(
        ["cmake", "--build", str(root / "build"), "--parallel", "4"],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stdout + built.stderr

    # ── the C face ───────────────────────────────────────────────────────
    ctest = subprocess.run(
        ["ctest", "--test-dir", str(root / "build"), "--output-on-failure"],
        capture_output=True,
        text=True,
    )
    assert ctest.returncode == 0, ctest.stdout + ctest.stderr

    # ── the Python face ──────────────────────────────────────────────────
    # Reverting only the C half left this green, so both are asserted.
    pytest_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            str(root / "src" / "demo" / "tests"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(root / "src")},
    )
    assert pytest_run.returncode == 0, pytest_run.stdout + pytest_run.stderr
    # A pytest run that collected nothing exits 5, not 0 -- but guard the
    # assertion anyway, since "no tests" must never read as "tests passed".
    assert "passed" in pytest_run.stdout, pytest_run.stdout
