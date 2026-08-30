"""gh-1212: what jm scaffolds and what jm claims about it must agree.

jm writes both the kernel body and the example beside it, so the example is not
a prediction — it is a statement about code jm just wrote, and it must be true
before the author touches anything. Once they implement their own logic the
example is theirs to maintain; until then it is jm's, and it was wrong.

Three separate defects, all of this one shape, all invisible until something
executed the examples:

* **`step()` claimed `0`.** The scaffolded kernel is a passthrough --
  ``return (T)x;`` -- so a scalar argument comes back out. It is zero only when
  there is nothing to pass through (a ``void`` argument generates from state; an
  array argument reduces a buffer), and those two were right all along.
* **`steps()` claimed `dtype('bool_')`.** ``np.bool_`` is the scalar TYPE; the
  dtype it makes is named ``bool``.
* **`steps()` claimed `dtype('uintp')` / `dtype('intp')` / `dtype('clongdouble')`.**
  Those names are platform-dependent aliases -- ``size_t`` is ``uint64`` on a
  64-bit build and ``uint32`` on a 32-bit one. No literal jm bakes is portable,
  so those emit an identity comparison instead.

`size_t` matters most of that last group: it is an ordinary return type, not an
exotic one.

**Why nothing caught them.** Module traversal cannot reach a C extension type,
so ``doctest.testmod(pkg.mod)`` reports ``attempted=0`` while
``DocTestFinder().find(Cls)`` finds nine examples per object. The examples were
shipped in ``__doc__``, executed by nothing, and a wrong one was
indistinguishable from a right one. The first test below is therefore the point
of this file: it BUILDS a project and RUNS them. A test that compared the
rendered string against the same table that rendered it would have passed
throughout.
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

from just_makeit._new import run as new_run  # noqa: E402
from just_makeit._init import run as init_run  # noqa: E402
from just_makeit._types import (  # noqa: E402
    _ALIAS_NP_DTYPES,
    _CTYPE_META,
    np_dtype_doctest_lines,
    np_dtype_name,
)

_NO_TOOLCHAIN = shutil.which("cmake") is None or (
    shutil.which("cc") is None and shutil.which("gcc") is None
)

#: (object, arg_type, return_type). Every branch the example builder takes:
#: scalar passthrough, a differing return type, the two shapes with nothing to
#: pass through (void arg, array arg), a void return, and each dtype-name
#: hazard (`bool`, and the platform-dependent `size_t` / long double complex).
SHAPES = [
    ("c_f", "float", "float"),
    ("c_u64", "uint64_t", "uint64_t"),
    ("c_c64", "float _Complex", "float _Complex"),
    ("c_mix", "float", "uint64_t"),
    ("c_bool", "float", "bool"),
    ("c_sz", "float", "size_t"),
    ("c_ld", "long double _Complex", "long double _Complex"),
    ("c_void", "void", "float"),
    ("c_arr", "float[]", "float"),
    ("c_vret", "float", "void"),
]


def _title(name: str) -> str:
    return "".join(p.title() for p in name.split("_"))


@pytest.mark.skipif(_NO_TOOLCHAIN, reason="needs cmake + a C compiler")
def test_every_scaffolded_example_passes_against_the_built_extension(
    tmp_path: Path,
):
    """Scaffold, compile, and execute. The only check that could have caught
    all three defects, and the only one that stays honest as shapes are added.
    """
    proj = tmp_path / "s"
    with contextlib.redirect_stdout(io.StringIO()):
        new_run(
            "s",
            proj,
            object_names=[],
            state_vars=[],
            arg_type="float",
            return_type="float",
            pytest_=False,
            pytest_benchmark_=False,
        )
        for obj, arg, ret in SHAPES:
            init_run(
                proj,
                obj,
                state_vars=[("gain", "float", "1.0f")],
                arg_type=arg,
                return_type=ret,
            )

    subprocess.run(
        [
            "cmake",
            "-B",
            "build",
            "-S",
            ".",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        cwd=proj,
        check=True,
        capture_output=True,
        timeout=900,
    )
    subprocess.run(
        ["cmake", "--build", "build", "--parallel", "4"],
        cwd=proj,
        check=True,
        capture_output=True,
        timeout=900,
    )

    # A subprocess, so the built extensions are never imported into the suite's
    # own interpreter -- and so a segfault in generated C fails this test
    # instead of taking the whole run down.
    script = f"""
import doctest, importlib, sys
sys.path.insert(0, {str(proj / "src")!r})
total = failed = 0
for mod, cls in {[(o, _title(o)) for o, _, _ in SHAPES]!r}:
    C = getattr(importlib.import_module("s." + mod), cls)
    r = doctest.DocTestRunner(verbose=False)
    for t in doctest.DocTestFinder().find(C):
        r.run(t)
    total += r.tries
    failed += r.failures
print("TOTAL", total, failed)
sys.exit(1 if failed else 0)
"""
    res = subprocess.run(
        [sys.executable, "-c", script],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    line = [ln for ln in res.stdout.splitlines() if ln.startswith("TOTAL")]
    assert line, f"harness produced no total:\n{res.stdout}\n{res.stderr}"
    _, total, failed = line[0].split()

    # An empty set reads as green, and these examples were unexecuted for the
    # whole of their existence -- so require that some actually ran.
    assert int(total) >= 60, f"only {total} example(s) executed"
    assert res.returncode == 0, (
        f"{failed} of {total} scaffolded example(s) failed:\n"
        f"{res.stdout}\n{res.stderr}"
    )


def test_jm_agrees_with_numpy_about_every_dtype_name():
    """The dtype table, against numpy itself.

    jm does not depend on numpy, so `np_dtype_name` is jm's own claim about
    what numpy will say. This is the independent oracle for it, and it is what
    makes the `bool_` class of mistake a one-time event rather than a
    recurring one. An alias is exempt because it has no single answer -- and
    that exemption is checked too, so a type cannot be parked in the alias set
    to silence a mismatch that is really a wrong name.
    """
    np = pytest.importorskip("numpy")
    checked = 0
    for ctype, meta in sorted(_CTYPE_META.items()):
        pt = meta.get("py_type")
        if not isinstance(pt, str) or not pt.startswith("np."):
            continue
        checked += 1
        real = np.dtype(eval(pt, {"np": np})).name
        if pt in _ALIAS_NP_DTYPES:
            assert real != pt.replace("np.", ""), (
                f"{ctype}: {pt} is in _ALIAS_NP_DTYPES but its dtype name "
                f"({real!r}) is stable here — it belongs in _NP_DTYPE_NAME "
                f"with the right name, not exempted"
            )
            continue
        assert np_dtype_name(pt) == real, (
            f"{ctype}: jm's example would say dtype({np_dtype_name(pt)!r}) "
            f"but numpy says dtype({real!r})"
        )
    assert checked >= 15, f"only {checked} type(s) examined"


def test_an_alias_states_an_identity_and_a_fixed_width_type_states_a_name():
    """The two forms, pinned. The identity form is what makes a
    platform-dependent dtype assertable at all."""
    assert np_dtype_doctest_lines("np.float32") == [
        "    >>> y.dtype",
        "    dtype('float32')",
    ]
    assert np_dtype_doctest_lines("np.bool_") == [
        "    >>> y.dtype",
        "    dtype('bool')",
    ]
    assert np_dtype_doctest_lines("np.uintp") == [
        "    >>> y.dtype == np.uintp",
        "    True",
    ]


def test_every_ctype_carrying_a_zero_carries_the_matching_one():
    """`py_one` is what the scaffolded passthrough returns, and it is only
    meaningful beside `py_zero` — the two are read by the same branch, so a
    type with one and not the other renders a KeyError-shaped default."""
    missing = [
        t
        for t, m in _CTYPE_META.items()
        if "py_zero" in m and "py_one" not in m
    ]
    assert not missing, f"py_zero without py_one: {missing}"
