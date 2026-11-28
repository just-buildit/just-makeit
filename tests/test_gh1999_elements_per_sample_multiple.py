"""An interleaved array whose length is not whole samples is refused (gh-1999).

``elements_per_sample = E`` divides an array's element count into the
kernel's sample count, and the division floors: five ``int16_t`` elements
under E = 2 reached the kernel as two samples, the fifth element dropped
without a word. Every E-aware acquisition did it, because each took its
length from ``_coerce.array_count_c`` and nothing checked the remainder.

Each now refuses such a length with a ``ValueError`` naming the param, its
length and E, through the one emitter, ``_coerce.array_shape_guard``. And
the examples jm synthesizes -- the generated tests' constructor call, a
method's doctest, a function module's contract test -- pass whole samples,
or the scaffold's own suite would fail on the guard.

GATE, two halves:

* **built**: one project carrying E = 3 on every face that reads it -- a
  method param (required and defaulted), a module function param (required
  and defaulted), a variable-output method's sizing param, and a
  constructor's array (required, defaulted and ``optional`` dispatch) --
  compiled; each face refuses 4 elements and accepts 6, and the project's
  own generated suite passes.
* **source**: no builder calls the rank or the multiple guard directly; a
  face that guarded rank alone is how the interleave went unchecked on all
  of them at once.
"""

from __future__ import annotations

import ast
import contextlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from just_makeit._apply import run as apply_run  # noqa: E402
from just_makeit._new import run as new_run  # noqa: E402
from _cmake import PYTHON3_ARG

_SRC = Path(__file__).parent.parent / "src" / "just_makeit"


def _skip_reason() -> "str | None":
    if not shutil.which("cmake"):
        return "cmake not found"
    if default_cc() is None:
        return "no C compiler found"
    try:
        import numpy  # noqa: F401
    except ImportError:
        return "numpy not importable"
    return None


_SKIP = _skip_reason()


def _quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*args, **kwargs)


_EPS = '{name = "x", type = "int16_t[]", elements_per_sample = 3'

#: Every face that reads `elements_per_sample`, at E = 3.
_FRAGMENT = f"""\
[module.ops]
objects = []

[[module.ops.functions]]
name = "f"
return_type = "int"
params = [{_EPS}}}]

[[module.ops.functions]]
name = "g"
return_type = "int"
params = [{_EPS}, default = "[]"}}]

[w]
arg_type = "int16_t"
return_type = "int16_t"
mutable = "false"
no_state = "false"
no_step = "false"

[[w.state]]
name = "gain"
type = "float"
default = "0.0f"

[[w.init_params]]
name = "h"
type = "int16_t[]"
elements_per_sample = 3

[[w.init_params]]
name = "d"
type = "int16_t[]"
elements_per_sample = 3
default = "[]"

[[w.methods]]
name = "m"
arg_type = "void"
return_type = "int"
params = [{_EPS}}}]

[[w.methods]]
name = "md"
arg_type = "void"
return_type = "int"
params = [{_EPS}, default = "[]"}}]

[[w.methods]]
name = "v"
arg_type = "void"
return_type = "int16_t"
variable_output = true
params = [{_EPS}}}]

[u]
arg_type = "int16_t"
return_type = "int16_t"
mutable = "false"
no_state = "false"
no_step = "false"

[[u.state]]
name = "gain"
type = "float"
default = "0.0f"

[[u.init_params]]
name = "o"
type = "int16_t[]"
elements_per_sample = 3
optional = true
create_fn = "q_u_create_o"
"""

#: (face, the E = 3 param, the call with `{n}` elements of it).
_FACES = [
    ("constructor", "h", "W(h=a({n}))"),
    ("defaulted constructor", "d", "W(h=a(3), d=a({n}))"),
    ("optional constructor", "o", "U(o=a({n}))"),
    ("method", "x", "W(h=a(3)).m(a({n}))"),
    ("defaulted method", "x", "W(h=a(3)).md(a({n}))"),
    ("variable-output method", "x", "W(h=a(3)).v(a({n}))"),
    ("function", "x", "ops.f(a({n}))"),
    ("defaulted function", "x", "ops.g(a({n}))"),
]
_IDS = [face for face, _, _ in _FACES]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The project above, applied from its fragment and compiled."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = tmp_path_factory.mktemp("gh1999") / "q"
    _quiet(new_run, "q", dest)
    fragment = dest.parent / "faces.toml"
    fragment.write_text(_FRAGMENT, encoding="utf-8")
    _quiet(apply_run, dest, fragment)
    build = dest / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(dest),
            "-B",
            str(build),
            PYTHON3_ARG,
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return dest


def _outcome(dest: Path, call: str) -> str:
    """What *call* does: ``ok``, or the exception it raised, one line."""
    body = (
        "import numpy as np\n"
        "from q import ops\n"
        "from q.w import W\n"
        "from q.u import U\n"
        "def a(n): return np.zeros(n, np.int16)\n"
        "try:\n"
        f"    {call}\n"
        "    print('ok')\n"
        "except Exception as e:\n"
        "    print(type(e).__name__ + ': ' + str(e))\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", body],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout.strip()


@pytest.mark.parametrize(("face", "pname", "call"), _FACES, ids=_IDS)
def test_a_partial_sample_is_refused(built, face, pname, call):
    """4 elements are a sample and a third: refused, naming all three."""
    got = _outcome(built, call.format(n=4))
    assert got == (
        f"ValueError: {pname} has 4 elements, not a multiple of "
        f"elements_per_sample = 3"
    ), f"{face}: {got}"


@pytest.mark.parametrize(("face", "pname", "call"), _FACES, ids=_IDS)
def test_whole_samples_are_accepted(built, face, pname, call):
    """6 elements are two samples: the guard lets them through."""
    assert _outcome(built, call.format(n=6)) == "ok", f"{face} ({pname})"


def test_the_generated_suite_passes(built):
    """The scaffold's own tests construct and call with whole samples.

    Its constructor call, its method doctests and the function module's
    contract test all synthesize an array; a length that is not a multiple
    of E would now fail on the guard in the project's first ``make test``.
    """
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "src",
        ],
        cwd=built,
        env={**os.environ, "PYTHONPATH": str(built / "src")},
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout[-3000:]
    assert " passed" in r.stdout, r.stdout[-1000:]
    # The function contract test turns a ValueError into a skip, so a
    # length the guard refuses reads as a skip, not a failure.
    assert "skipped" not in r.stdout, r.stdout[-1000:]


def test_the_runtime_doc_examples_pass(built):
    """Every ``>>>`` example on the built classes runs, under the guard.

    A method's doctest passes an array param four samples -- four times E
    elements; four elements under E = 3 would raise in ``help()``'s
    example. ``np`` is seeded because ``step``'s example uses it without
    importing it, which is not this issue's.
    """
    body = (
        "import doctest\n"
        "import numpy as np\n"
        "from q.w import W\n"
        "from q.u import U\n"
        "fails = []\n"
        "for cls in (W, U):\n"
        "    for name in dir(cls):\n"
        "        doc = getattr(getattr(cls, name), '__doc__', '') or ''\n"
        "        if '>>>' not in doc:\n"
        "            continue\n"
        "        t = doctest.DocTestParser().get_doctest(\n"
        "            doc, {'np': np}, f'{cls.__name__}.{name}', None, 0)\n"
        "        r = doctest.DocTestRunner(\n"
        "            optionflags=doctest.ELLIPSIS).run(t, out=lambda s: None)\n"
        "        if r.failed:\n"
        "            fails.append(t.name)\n"
        "print(fails)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", body],
        cwd=built,
        env={**os.environ, "PYTHONPATH": str(built / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr[-1000:]
    assert r.stdout.strip() == "[]", r.stdout


def test_every_guard_goes_through_the_shape_guard():
    """No builder calls the rank or the multiple guard directly.

    ``array_shape_guard`` emits both; a site that called the rank guard
    alone is how every face read ``rank`` and none ``elements_per_sample``.
    """
    direct = {"array_rank_guard", "array_multiple_guard"}
    found = []
    for path in sorted(_SRC.rglob("*.py")):
        # The templates are not builders, and their `<<slot>>`s do not
        # parse; the examples are generated projects.
        rel = path.relative_to(_SRC)
        if path.name == "_coerce.py" or rel.parts[0] in (
            "templates",
            "examples",
        ):
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in direct:
                found.append(f"{path.relative_to(_SRC)}:{node.lineno}")
            elif isinstance(node, ast.Name) and node.id in direct:
                found.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert not found, (
        "call _coerce.array_shape_guard, which emits every shape guard: "
        + ", ".join(found)
    )
