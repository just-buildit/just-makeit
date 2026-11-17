"""A method or function array param with ``default = "[]"`` (gh-2657).

The default made the argument optional -- the parse format put it after the
``|`` -- so an omitted argument left its ``<p>_obj`` local ``NULL``. Both
param emitters (``_context/_parse._build_params_parse`` for a method,
``_render._build_params_parse`` for a module function) then acquired it
unconditionally, handing ``PyArray_FromAny`` the ``NULL``: a segfault on the
first call that left it out. doppler hit it on a method; the module-function
peer had the same line. The ``.pyi`` of an object's method meanwhile
rendered the parameter as required.

Now both take the contract an init-param's ``default = "[]"`` has had since
gh-611, through one emitter (``_coerce.defaulted_array_acq``): omitted or
``None``, the array reaches the C call as ``NULL`` with length ``0``, and
every release of it is ``Py_XDECREF``. A default the binding cannot honour
-- an array default other than ``"[]"``, any default on a ``path`` or
``bytes`` param (which crashed the renderer with ``KeyError``) -- is refused
at load.

GATE. A compiled object and module function whose kernels report whether
the pointer was ``NULL`` and the length they were handed:

* omitted, ``None``, positional and keyword calls each reach the C with
  what was passed, on both faces;
* a refusal inside the branch (a rank guard, an unconvertible argument)
  raises, and releases the array acquired before it -- repeated 300 times
  with the refcount compared;
* a defaulted ``out`` buffer is filled when passed and skipped when not;
* both stubs mark the parameter optional;
* the refused defaults stop ``apply`` naming the parameter.
"""

from __future__ import annotations

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


def _refusal(dest: Path, fragment: str) -> str:
    """The ``error:`` text ``apply`` stops on for *fragment*, or ``""``."""
    frag = dest.parent / "frag.toml"
    frag.write_text(fragment, encoding="utf-8")
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.redirect_stderr(err):
                apply_run(dest, frag)
    except SystemExit as e:
        assert e.code, err.getvalue()
        return err.getvalue()
    return ""


def _apply_fragment(base: Path, fragment: str) -> Path:
    """A fresh project with *fragment* applied to it."""
    dest = base / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    frag = base / "frag.toml"
    frag.write_text(fragment, encoding="utf-8")
    _quiet(apply_run, dest, frag)
    return dest


#: Each kernel returns `1000 * (xs == NULL) + 10 * xs_len + ys_len`, so one
#: integer says whether the pointer was NULL and both lengths. `ys` is
#: required and FIRST, so a refusal of `xs` happens with `ys_arr` held and
#: must release it. `put` is doppler's shape (complex, after a scalar);
#: `ranked` puts the rank guard inside the branch; `fill` is a defaulted
#: `out` buffer.
_REPORT = "return (xs == NULL) * 1000 + (int)xs_len * 10 + (int)ys_len;"

_FRAGMENT = f"""\
[thing]
arg_type = "void"
return_type = "void"
no_state = "true"
no_step = "true"
no_reset = "true"
class_name = "Thing"

[[thing.methods]]
name = "put"
arg_type = "void"
return_type = "int"
params = [
    {{ name = "n", type = "uint32_t" }},
    {{ name = "ys", type = "float[]" }},
    {{ name = "xs", type = "float _Complex[]", default = "[]" }},
]
impl = "(void)state; (void)n; (void)ys; {_REPORT}"

[[thing.methods]]
name = "ranked"
arg_type = "void"
return_type = "int"
params = [
    {{ name = "ys", type = "float[]" }},
    {{ name = "xs", type = "float[]", default = "[]", rank = 1 }},
]
impl = "(void)state; (void)ys; {_REPORT}"

[[thing.methods]]
name = "fill"
arg_type = "void"
return_type = "int"
params = [
    {{ name = "ys", type = "float[]" }},
    {{ name = "xs", type = "float[]", default = "[]", out = true }},
]
impl = "(void)state; (void)ys; for (size_t i = 0; i < xs_len; i++) xs[i] = 7.0f; {_REPORT}"

[module.ops]
objects = []

[[module.ops.functions]]
name = "total"
return_type = "int"
impl = "(void)ys; {_REPORT}"

[[module.ops.functions.params]]
name = "ys"
type = "float[]"

[[module.ops.functions.params]]
name = "xs"
type = "float _Complex[]"
default = "[]"
rank = 1
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The object and the module function above, applied and compiled."""
    if _SKIP:
        pytest.skip(_SKIP)
    dest = _apply_fragment(tmp_path_factory.mktemp("gh2657"), _FRAGMENT)
    build = dest / "build"
    for cmd in (
        [
            "cmake",
            "-S",
            str(dest),
            "-B",
            str(build),
            f"-DPython3_EXECUTABLE={sys.executable}",
        ],
        ["cmake", "--build", str(build)],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, f"{cmd[:2]}:\n{r.stdout}\n{r.stderr}"
    return dest


def _outcome(dest: Path, body: str) -> str:
    """What *body* prints, or ``CRASH <rc>`` when the interpreter died --
    which is what an omitted argument did before the fix."""
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\nimport numpy as np\nfrom p.thing import Thing\n"
            "from p.ops import total\n"
            "ys = np.ones(3, np.float32)\n"
            "xs = np.ones(2, np.complex64)\n"
            "fs = np.ones(2, np.float32)\n" + body,
        ],
        cwd=dest,
        env={**os.environ, "PYTHONPATH": str(dest / "src")},
        capture_output=True,
        text=True,
        timeout=300,
    )
    if r.returncode != 0:
        return f"CRASH {r.returncode}: {r.stderr.strip()[-300:]}"
    return r.stdout.strip()


#: (call, what the kernel reported). 1003: NULL, length 0, `ys` of 3.
CALLS = [
    ("Thing().put(1, ys)", "1003"),
    ("Thing().put(1, ys, None)", "1003"),
    ("Thing().put(1, ys, xs)", "23"),
    ("Thing().put(1, ys, xs=xs)", "23"),
    ("Thing().put(n=1, ys=ys)", "1003"),
    ("Thing().ranked(ys)", "1003"),
    ("Thing().ranked(ys, fs)", "23"),
    ("Thing().fill(ys)", "1003"),
    ("total(ys)", "1003"),
    ("total(ys, None)", "1003"),
    ("total(ys, xs)", "23"),
    ("total(ys, xs=xs)", "23"),
]


@pytest.mark.parametrize(("call", "want"), CALLS, ids=[c for c, _ in CALLS])
def test_the_kernel_sees_what_the_caller_passed(built, call, want):
    assert _outcome(built, f"print({call})") == want


def test_a_passed_out_buffer_is_filled(built):
    got = _outcome(
        built,
        "o = np.zeros(2, np.float32)\n"
        "print(Thing().fill(ys, o), o.tolist())\n",
    )
    assert got == "23 [7.0, 7.0]", got


#: (call, the ValueError/TypeError text). Each refusal happens inside the
#: branch with `ys_arr` held.
REFUSALS = [
    ("Thing().ranked(ys, np.ones((2, 2), np.float32))", "1-D"),
    ("total(ys, np.ones((2, 2), np.complex64))", "1-D"),
    ("Thing().put(1, ys, 'not a number')", ""),
    ("Thing().fill(ys, np.zeros(2, np.float64))", ""),
]


@pytest.mark.parametrize(
    ("call", "says"), REFUSALS, ids=[c for c, _ in REFUSALS]
)
def test_a_refusal_in_the_branch_releases_what_it_holds(built, call, says):
    """300 refusals leave `ys`'s refcount where it started: the bailout
    released `ys_arr`, and released it once."""
    got = _outcome(
        built,
        "before = sys.getrefcount(ys)\n"
        "msgs = set()\n"
        "for _ in range(300):\n"
        "    try:\n"
        f"        {call}\n"
        "        msgs.add('ACCEPTED')\n"
        "    except (ValueError, TypeError) as e:\n"
        "        msgs.add(type(e).__name__)\n"
        "        last = str(e)\n"
        "print(sorted(msgs), sys.getrefcount(ys) == before, last)\n",
    )
    assert "ACCEPTED" not in got and "CRASH" not in got, got
    assert " True " in got, got
    assert says in got, got


def test_both_stubs_mark_the_parameter_optional(built):
    thing = (built / "src/p/thing.pyi").read_text("utf-8")
    ops = (built / "src/p/ops/ops.pyi").read_text("utf-8")
    assert "xs: npt.NDArray[np.complex64] = ...," in thing, thing
    assert "xs: npt.NDArray[np.complex64] = ...," in ops, ops


# -- the refusals -------------------------------------------------------------

_HEAD = """\
[thing]
arg_type = "void"
return_type = "void"
no_state = "true"
no_step = "true"
no_reset = "true"

[[thing.methods]]
name = "put"
arg_type = "void"
return_type = "int"
"""

REFUSED = [
    (
        'params = [{name = "xs", type = "float[]", default = "[1, 2]"}]',
        "'xs' declares default = '[1, 2]'; an array param takes only",
    ),
    (
        'params = [{name = "xs", type = "float[]", default = "NULL"}]',
        "'xs' declares default = 'NULL'; an array param takes only",
    ),
    (
        'params = [{name = "p", type = "path", default = "NULL"}]',
        "'p' is a 'path' param and cannot declare a default",
    ),
    (
        'params = [{name = "b", type = "bytes", default = "x"}]',
        "'b' is a 'bytes' param and cannot declare a default",
    ),
]


@pytest.mark.parametrize(
    ("params", "says"), REFUSED, ids=["array", "array-null", "path", "bytes"]
)
def test_a_default_the_binding_cannot_honour_is_refused(
    tmp_path, params, says
):
    dest = tmp_path / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    got = _refusal(dest, _HEAD + params + "\n")
    assert says in got, got


def test_a_module_function_default_is_refused_too(tmp_path):
    dest = tmp_path / "p"
    _quiet(new_run, "p", dest, c_prefix=None)
    got = _refusal(
        dest,
        "[module.ops]\nobjects = []\n\n"
        '[[module.ops.functions]]\nname = "f"\nreturn_type = "int"\n'
        'params = [{name = "xs", type = "int[]", default = "[0]"}]\n',
    )
    assert "'xs' declares default = '[0]'" in got, got
