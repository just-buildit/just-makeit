"""gh-2273: a teardown during argument conversion must not reach the kernel.

A generated method checks its handle once, at the top, and then converts its
arguments. Converting an array argument can run Python (``__array__``), and
that Python can call ``destroy()`` on the same object: the state is freed,
``self->handle`` is NULL, and the method then handed the kernel a NULL
state -- a segfault, with or without ``nogil``.

The fix is one emitter's: `_methods._nogil_call` runs the guard again after
the last conversion (`_inflight.recheck`), releasing what the call acquired
first, and raising the same ``RuntimeError("destroyed")`` the top guard does.
`max_out`, the other read of the state between the conversion and the
kernel, is rechecked the same way.

The gates, cheapest first:

1. a source gate: every call of the three emitters in ``src/`` states what it
   holds (``release=``), so a kernel call cannot be added that forgets the
   recheck's cleanup -- registration-free over the source;
2. the rendered C of every shape ``jm method`` accepts: each kernel call is
   preceded by the recheck, and no conversion sits between the two;
3. a compiled object, a held-GIL method and a ``variable_output`` method,
   each handed an ``__array__`` that destroys the object: ``RuntimeError``,
   not a signal.
"""

# gh-1591: this file's hand-written C spells jm's bare derived names, so its
# projects opt out of the prefix `jm new` now defaults to.

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _compilers import default_cc
from _jmrun import run_cli

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from just_makeit import _inflight  # noqa: E402
from test_gh2259_batch_method_faces import SHAPES  # noqa: E402

#: The functions a kernel call is built inside (gh-2259's `EMITTERS`).
EMITTERS = frozenset(
    {"_nogil_call", "_kernel_call_block", "_single_kernel_block"}
)

_NO_TOOLCHAIN = shutil.which("cmake") is None or default_cc() is None


def _ok(*args: str, cwd: Path) -> None:
    r = run_cli(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def _emitter_calls() -> list[tuple[Path, ast.Call]]:
    found = []
    for path in sorted((SRC / "just_makeit").rglob("*.py")):
        if "templates" in path.parts:
            continue  # `<<token>>` files: not Python until rendered
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in EMITTERS
            ):
                found.append((path, node))
    return found


class TestEveryCallStatesWhatItHolds:
    def test_every_emitter_call_passes_release(self):
        """Registration-free over ``src/``.

        The recheck is rendered by the emitter, so a call site can neither
        omit it nor spell its own -- but it can only release what it names.
        ``release`` is keyword-only with no default, so a call that does not
        state it is a ``TypeError`` at render time; this names the line
        before a render has to find it.
        """
        calls = _emitter_calls()
        stray = [
            f"{p.name}:{n.lineno}"
            for p, n in calls
            if not any(k.arg == "release" for k in n.keywords)
        ]
        assert not stray, f"emitter call(s) without release=: {stray}"
        # Armed: the walk found the calls it is about.
        assert len(calls) >= 15, len(calls)

    def test_release_has_no_default(self):
        import inspect

        from just_makeit._context._methods import (
            _kernel_call_block,
            _nogil_call,
            _single_kernel_block,
        )

        for fn in (_nogil_call, _kernel_call_block, _single_kernel_block):
            p = inspect.signature(fn).parameters["release"]
            assert p.kind is p.KEYWORD_ONLY, fn
            assert p.default is p.empty, fn


@pytest.fixture(scope="module")
def every_shape(tmp_path_factory) -> str:
    root = tmp_path_factory.mktemp("recheck_shapes")
    _ok("new", "mp", "--no-c-prefix", cwd=root)
    proj = root / "mp"
    _ok("object", "o", "--state", "k:double:0.0", cwd=proj)
    for name, flags in SHAPES.items():
        _ok("method", "o", name, *flags, cwd=proj)
    return (proj / "native/src/o/o_ext.c").read_text(encoding="utf-8")


def _wrapper(ext: str, name: str) -> str:
    m = re.search(rf"\nO_{name}\(.*?\n\}}\n", ext, re.S)
    assert m, name
    return m.group(0)


#: What runs Python while a method converts its arguments.
_CONVERSION = re.compile(
    r"jm_array_arg\(|PyArg_Parse|PyArray_FROM|PyArray_SimpleNew|"
    r"PyArray_EMPTY|PyLong_As|PyFloat_As|_get_dtype\(|malloc\("
)

#: A kernel call, as the rendered wrapper spells it: the state first.
_STATE_READ = re.compile(r"\bo_(?P<n>\w+)\((?:self->handle|_jm_h)\b")


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_kernel_call_is_rechecked(every_shape, shape):
    body = _wrapper(every_shape, shape)
    calls = [
        m
        for m in _STATE_READ.finditer(body)
        # `max_out` and the like are rechecked on their own line below.
        if not m.group("n").endswith(("_max_out", "_consumed"))
    ]
    assert calls, body
    for call in calls:
        before = body[: call.start()]
        guard = before.rfind("if (!self->handle) {")
        assert guard > before.find("}"), (
            f"o_{shape}() is called with no recheck after the top guard:\n"
            f"{body}"
        )
        between = before[guard:]
        # The recheck refuses with the top guard's own error...
        assert 'PyExc_RuntimeError, "destroyed"' in between.split("}")[0], body
        # ...and nothing that can run Python sits between it and the call.
        after_guard = between[between.index("}") :]
        assert not _CONVERSION.search(after_guard), (
            f"a conversion sits between the recheck and o_{shape}():\n"
            f"{after_guard}"
        )


def test_max_out_is_rechecked(every_shape):
    body = _wrapper(every_shape, "vo_arg")
    for m in re.finditer(r"o_vo_arg_max_out\(self->handle", body):
        before = body[: m.start()]
        assert before.rstrip().endswith("}") or "size_t _omax" in body, body
        guard = before.rfind("if (!self->handle) {")
        assert guard > 0
        assert not _CONVERSION.search(before[guard:]), before[guard:]


def test_recheck_releases_before_it_refuses():
    c = _inflight.recheck("self", "k(self->handle)", "Py_DECREF(a);")
    assert c.index("Py_DECREF(a);") < c.index("PyErr_SetString")
    assert c.rstrip().endswith("}")


_PROBE = """\
import numpy as np
from p import W

w = W(k=1)
w.eat(np.ones(2, np.float32))


def sneaky(w):
    class Sneaky:
        def __array__(self, dtype=None, copy=None):
            w.destroy()
            return np.ones(3, dtype=np.float32)
    return Sneaky()


for call, kw in (("eat", {}), ("vo", {}), ("vo_out", {"out": 1})):
    w = W(k=1)
    if kw:
        kw = {"out": np.empty(8, np.float32)}
    try:
        getattr(w, call.split("_")[0])(sneaky(w), **kw)
    except RuntimeError as e:
        print(call, "RuntimeError", e)
    else:
        print(call, "no error")
"""


@pytest.mark.slow
@pytest.mark.skipif(_NO_TOOLCHAIN, reason="no cmake / C compiler")
def test_a_teardown_in_conversion_is_an_error_not_a_crash(tmp_path):
    """The reproduction in the issue, compiled and run.

    Before the recheck the child died with SIGSEGV (returncode -11) on the
    first ``eat(Sneaky())``; ``vo`` read the NULL handle in ``max_out``.
    """
    _ok("new", "p", "--no-c-prefix", cwd=tmp_path)
    proj = tmp_path / "p"
    _ok("object", "w", "--state", "k:int:0", cwd=proj)
    _ok(
        "method",
        "w",
        "eat",
        "--arg-type",
        "float[]",
        "--return-type",
        "int",
        cwd=proj,
    )
    _ok(
        "method",
        "w",
        "vo",
        "--arg-type",
        "float[]",
        "--return-type",
        "float",
        "--variable-output",
        cwd=proj,
    )
    # The scaffolded kernels never touch the state, so a NULL handed to one
    # would pass. Each one reads it, as a real kernel does, so a missing
    # recheck at ANY of the three reads (`eat`, `vo_max_out`, `vo`) is a
    # SIGSEGV here, and each site is its own sabotage.
    core = proj / "native" / "src" / "w" / "w_core.c"
    text = core.read_text(encoding="utf-8")
    for fn in ("w_eat", "w_vo_max_out", "w_vo"):
        text, hits = re.subn(
            rf"(\n{fn}\(w_state_t \*state[^)]*\)\n\{{\n)",
            r"\1    volatile int _k = state->k; (void)_k;\n",
            text,
            count=1,
        )
        assert hits == 1, fn
    core.write_text(text, encoding="utf-8")
    build = subprocess.run(
        ["make", f"PYTHON={sys.executable}"],
        cwd=proj,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert build.returncode == 0, build.stderr[-3000:]
    run = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=proj,
        env={"PYTHONPATH": str(proj / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    assert "eat RuntimeError destroyed" in run.stdout, run.stdout
    assert "vo RuntimeError destroyed" in run.stdout, run.stdout
    assert "vo_out RuntimeError destroyed" in run.stdout, run.stdout


def _as_rendered_before_the_recheck(ext: str) -> str:
    """*ext* as a pre-gh-2273 jm wrote it: each wrapper's rechecks removed.

    The first ``if (!self->handle)`` of a wrapper is its top guard and stays;
    every later one is a recheck (or the ``max_out`` one) and goes.
    """
    block = re.compile(
        r"[ \t]*if \(!self->handle\) \{\n(?:[^{}]*\n)*?[ \t]*\}\n"
    )
    out = []
    for fn in ext.split("\nstatic PyObject *\n"):
        seen = [0]

        def drop(m: "re.Match[str]") -> str:
            seen[0] += 1
            return m.group(0) if seen[0] == 1 else ""

        out.append(block.sub(drop, fn))
    return "\nstatic PyObject *\n".join(out)


@pytest.mark.parametrize(
    "owner,call,release,gone",
    [
        ("self", "k(self->handle, x)", "Py_DECREF(a);", "destroyed"),
        ("self", "k(self->handle)", "", "destroyed"),
        ("w", "k(w->state)", "Py_DECREF(a); Py_DECREF(b);", "w was freed"),
    ],
)
def test_the_strip_removes_exactly_what_recheck_renders(
    owner, call, release, gone
):
    """One spelling of the guard: `strip_rechecks` is held to `recheck`.

    Strings are masked where `_docsync` reads a body, so the guard's message
    is blanked; the structure is what is matched. The first guard in a body
    is the method's top guard and stays, the recheck after it goes.
    """
    from just_makeit import _docsync

    block = _inflight.recheck(owner, call, release, gone)
    assert block
    cond = "!self->handle" if owner == "self" else f"{owner}->destroyed"
    top = (
        f'    if ({cond}) {{\n        PyErr_SetString(E, "top");\n'
        "        return NULL;\n    }\n"
    )
    masked = _docsync._code_mask(top + "    parse();\n" + block + "    k();\n")
    left = _inflight.strip_rechecks(masked)
    assert left.count("PyErr_SetString") == 1, left
    assert "k();" in left and "parse();" in left, left


def test_a_fragment_rendered_before_the_recheck_is_not_drift(every_shape):
    """A sacred module fragment keeps its body on `apply`; `status` compares
    it to a fresh render. The recheck is jm's own guard, so a fragment
    without it must not be told its "result shape needs raises" (the
    `stale_project` example's `upgrade.golden` caught the first version).
    """
    from just_makeit import _docsync

    old = _as_rendered_before_the_recheck(every_shape)
    assert old != every_shape, "nothing was removed: the test is not armed"
    assert _docsync.signature_drift_details(old, every_shape) == {}
